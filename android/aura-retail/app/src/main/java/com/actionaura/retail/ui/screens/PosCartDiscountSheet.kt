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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import com.actionaura.retail.ui.i18n.Currency
import com.actionaura.retail.ui.i18n.money
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.pos.CartDiscount
import com.actionaura.retail.ui.pos.CartDiscountKind
import com.actionaura.retail.ui.pos.DISCOUNT_REASONS
import com.actionaura.retail.ui.pos.DISCOUNT_REASON_MAX
import com.actionaura.retail.ui.pos.DraftCheck
import com.actionaura.retail.ui.pos.REASON_OTHER
import com.actionaura.retail.ui.pos.REASON_ROUND_DOWN
import com.actionaura.retail.ui.pos.checkDraft
import com.actionaura.retail.ui.pos.discountReasonText
import com.actionaura.retail.ui.pos.estimateAfterAmountMinor
import com.actionaura.retail.ui.pos.EstimateLine
import com.actionaura.retail.ui.pos.fromMinor
import com.actionaura.retail.ui.pos.minorToInput
import com.actionaura.retail.ui.pos.padEdit
import com.actionaura.retail.ui.pos.parseAmountMinor
import com.actionaura.retail.ui.pos.roundDownTargets
import com.actionaura.retail.ui.pos.KEY_BACK

/**
 * The sale-level discount sheet (the desktop's UI-3a "Discount" sheet): an AMOUNT off, or a NEW
 * TOTAL ("make it 10.000"), with the "Round total down to" chips and a reason.
 *
 * Everything on it is the phone's ESTIMATE and says so: the server spreads the discount over the
 * lines, adds tax and answers with the real total. What the sheet hands back is only the intent --
 * [CartDiscount] (kind, amount in minor units, reason). It refuses an empty or impossible request
 * on the spot (shared [checkDraft]); anything subtler (a target the lines cannot reach exactly) the
 * server refuses with the nearest totals named, and the till shows that.
 */
@Composable
internal fun CartDiscountSheet(
    estimateLines: List<EstimateLine>,
    grossSubtotalMinor: Long,
    baseTotalMinor: Long,
    dp: Int,
    current: CartDiscount?,
    /** Non-null = a per-line or whole-sale % already sits on the cart; the server refuses the pair. */
    blockedBy: String?,
    onApply: (kind: CartDiscountKind, minor: Long, reason: String) -> Unit,
    onRemove: () -> Unit,
    onDismiss: () -> Unit,
) {
    var kind by remember { mutableStateOf(current?.kind ?: CartDiscountKind.AMOUNT) }
    var text by remember { mutableStateOf(current?.let { minorToInput(it.minor, dp) } ?: "") }
    var preset by remember { mutableStateOf<String?>(null) }
    var other by remember { mutableStateOf("") }
    val valueMinor = parseAmountMinor(text, dp) ?: 0L
    val check = checkDraft(kind, valueMinor, grossSubtotalMinor, baseTotalMinor)
    val reason = discountReasonText(preset, other) { tr(it) }
    val estimate = when {
        check != DraftCheck.OK -> null
        kind == CartDiscountKind.AMOUNT -> estimateAfterAmountMinor(estimateLines, valueMinor, dp)
        else -> valueMinor
    }
    var error by remember { mutableStateOf<String?>(null) }

    // Full height from the start: these sheets are taller than the half-height a sheet opens at,
    // which hid the keypad and the Apply button (found on the emulator).
    val sheet = androidx.compose.material3.rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(
            Modifier.padding(horizontal = 20.dp).padding(bottom = 24.dp).verticalScroll(rememberScrollState()),
        ) {
            Text(tr("Discount"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(selected = kind == CartDiscountKind.AMOUNT, onClick = { kind = CartDiscountKind.AMOUNT; text = ""; error = null },
                    label = { Text(Currency.symbol.ifBlank { "#" }) })
                FilterChip(selected = kind == CartDiscountKind.TARGET, onClick = { kind = CartDiscountKind.TARGET; text = ""; error = null },
                    label = { Text(tr("New total")) })
            }
            Spacer(Modifier.height(10.dp))
            Text(
                (Currency.symbol + " " + (text.ifEmpty { "0" })).trim(),
                style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.height(8.dp))

            val chips = roundDownTargets(baseTotalMinor, dp)
            if (chips.isNotEmpty()) {
                Text(tr("Round total down to"), style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    chips.forEach { m ->
                        FilterChip(
                            selected = kind == CartDiscountKind.TARGET && valueMinor == m,
                            onClick = { kind = CartDiscountKind.TARGET; text = minorToInput(m, dp); preset = REASON_ROUND_DOWN; error = null },
                            label = { Text(minorToInput(m, dp)) },
                        )
                    }
                }
                Spacer(Modifier.height(8.dp))
            }

            CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Ltr) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
                    Keypad(keys = CASH_KEYS, onKey = { text = padEdit(text, it, dp); error = null })
                }
            }
            Spacer(Modifier.height(10.dp))

            Text(tr("Reason"), style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                DISCOUNT_REASONS.forEach { r ->
                    FilterChip(selected = preset == r, onClick = { preset = if (preset == r) null else r; error = null },
                        label = { Text(tr(r)) })
                }
            }
            if (preset == REASON_OTHER) {
                OutlinedTextField(
                    value = other, onValueChange = { other = it.take(DISCOUNT_REASON_MAX) }, singleLine = true,
                    label = { Text(tr("Reason")) }, modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
                )
            }
            Spacer(Modifier.height(10.dp))

            // The estimate, labelled as one: the server's total replaces it the moment the sale returns.
            val note = when {
                estimate != null -> tr("Discount %s  →  Estimated total %s").format(
                    money(fromMinor(if (kind == CartDiscountKind.AMOUNT) valueMinor else (baseTotalMinor - valueMinor), dp)),
                    money(fromMinor(estimate, dp)),
                ) + "\n" + tr("An estimate. The shop confirms the real total when you charge.")
                else -> null
            }
            note?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            val problem = blockedBy ?: error ?: draftMessage(check, valueMinor)
            problem?.let {
                Spacer(Modifier.height(6.dp))
                Text(it, style = MaterialTheme.typography.bodySmall, color = com.actionaura.retail.ui.theme.Danger)
            }

            Spacer(Modifier.height(12.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                if (current != null) TextButton(onClick = onRemove) { Text(tr("Remove discount")) }
                OutlinedButton(onClick = onDismiss, modifier = Modifier.weight(1f).height(48.dp)) { Text(tr("Cancel")) }
                Button(
                    onClick = {
                        when {
                            blockedBy != null -> error = blockedBy
                            check != DraftCheck.OK -> error = draftMessage(check, valueMinor) ?: tr("Enter a discount first")
                            reason.isBlank() -> error = tr("Pick a reason first")
                            else -> onApply(kind, valueMinor, reason)
                        }
                    },
                    modifier = Modifier.weight(1f).height(48.dp),
                ) { Text(tr("Apply discount")) }
            }
        }
    }
}

private val CASH_KEYS: List<List<String>> = listOf(
    listOf("7", "8", "9"), listOf("4", "5", "6"), listOf("1", "2", "3"), listOf(".", "0", KEY_BACK),
)

private fun draftMessage(check: DraftCheck, valueMinor: Long): String? = when (check) {
    DraftCheck.OK -> null
    DraftCheck.EMPTY -> if (valueMinor == 0L) null else tr("Enter a discount first")
    DraftCheck.TOO_BIG -> tr("The discount cannot be more than the cart total before tax.")
    DraftCheck.TARGET_HIGH -> tr("The new total cannot be higher than the current total.")
}
