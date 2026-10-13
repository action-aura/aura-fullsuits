@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import com.actionaura.retail.net.pctText
import com.actionaura.retail.ui.i18n.parseNum
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.pos.DiscountAccess
import com.actionaura.retail.ui.pos.clampDiscountPct
import com.actionaura.retail.ui.pos.maxPct

// Discount controls for the till (ANDROID-2a; a line's own % and price moved into PosLineMenu.kt in ANDROID-3). Every rule (who may discount, how far)
// is decided in ui/pos/DiscountRules.kt; these composables only collect a percentage and
// show the limit. Nothing here prices a sale, and the server re-checks every request.

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

