package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.text.KeyboardOptions
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
import com.actionaura.retail.ui.i18n.money
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.pos.CartLine
import com.actionaura.retail.ui.pos.DISCOUNT_REASON_MAX
import com.actionaura.retail.ui.pos.DiscountAccess
import com.actionaura.retail.ui.pos.LineMenuError
import com.actionaura.retail.ui.pos.LineMenuOutcome
import com.actionaura.retail.ui.pos.applyLineMenu
import com.actionaura.retail.ui.pos.maxPct
import com.actionaura.retail.ui.theme.Danger

/**
 * One cart line's own menu: an item discount % and a price of its own (with the reason the server
 * insists on). The desktop's `_openLineMenu`. The rules are [applyLineMenu]; this only collects
 * the three boxes and shows the first problem.
 *
 * A price of its own is offered to EVERYONE who can sell: a cashier without `retail.discount` is
 * told up front that a manager's PIN will be asked at Charge (the sale itself asks, see
 * ManagerApprovalSheet), instead of the control being hidden and the rule discovered at the till.
 * The % box is shown only to a person [access] lets discount at all.
 */
@Composable
internal fun LineMenuDialog(
    productName: String,
    line: CartLine,
    listPrice: Double,
    access: DiscountAccess,
    holdsDiscountCapability: Boolean,
    dp: Int,
    onApply: (LineMenuOutcome) -> Unit,
    onDismiss: () -> Unit,
) {
    var pct by remember { mutableStateOf(line.ownPct?.let { pctText(it) } ?: "") }
    var price by remember { mutableStateOf(line.unitPriceOverride?.let { priceText(it, dp) } ?: "") }
    var why by remember { mutableStateOf(line.priceReason ?: "") }
    var error by remember { mutableStateOf<String?>(null) }
    val max = access.maxPct()
    LocalizedAlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(productName, maxLines = 2) },
        text = {
            Column {
                if (access !is DiscountAccess.None) {
                    OutlinedTextField(
                        value = pct, onValueChange = { pct = it; error = null }, singleLine = true,
                        label = { Text(tr("Item discount (%)")) },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                        supportingText = if (access is DiscountAccess.Limited) {
                            { Text(tr("Up to %s%%").format(pctText(max))) }
                        } else null,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Spacer(Modifier.height(8.dp))
                }
                OutlinedTextField(
                    value = price, onValueChange = { price = it; error = null }, singleLine = true,
                    label = { Text(tr("Price")) }, placeholder = { Text(money(listPrice)) },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    modifier = Modifier.fillMaxWidth(),
                )
                if (!holdsDiscountCapability) {
                    Text(tr("A different price needs a manager's PIN at Charge."),
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = why, onValueChange = { why = it.take(DISCOUNT_REASON_MAX); error = null }, singleLine = true,
                    label = { Text(tr("Reason")) }, modifier = Modifier.fillMaxWidth(),
                )
                error?.let {
                    Spacer(Modifier.height(6.dp))
                    Text(it, style = MaterialTheme.typography.bodySmall, color = Danger)
                }
            }
        },
        confirmButton = {
            Button(onClick = {
                val out = applyLineMenu(pct, price, why, listPrice, access, dp)
                when (out.error) {
                    null -> onApply(out)
                    LineMenuError.PCT_TOO_HIGH ->
                        error = tr("You can give at most %s%% discount. Ask a manager for more.").format(pctText(out.limitPct))
                    LineMenuError.PRICE_NEGATIVE -> error = tr("The price cannot be negative.")
                    LineMenuError.PRICE_INVALID -> error = tr("Enter a valid amount")
                    LineMenuError.REASON_REQUIRED -> error = tr("Pick a reason first")
                }
            }) { Text(tr("Apply")) }
        },
        dismissButton = {
            Row {
                if (line.ownPct != null || line.unitPriceOverride != null) {
                    TextButton(onClick = { onApply(LineMenuOutcome(null, null, null)) }) { Text(tr("Clear")) }
                }
                TextButton(onClick = onDismiss) { Text(tr("Cancel")) }
            }
        },
    )
}

private fun priceText(v: Double, dp: Int): String = java.math.BigDecimal(v.toString()).setScale(dp, java.math.RoundingMode.HALF_UP).toPlainString()
