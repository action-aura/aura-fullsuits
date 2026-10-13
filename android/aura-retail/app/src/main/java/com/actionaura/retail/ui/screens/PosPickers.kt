@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.retail.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.ModifierGroup
import com.actionaura.retail.net.Product
import com.actionaura.retail.ui.i18n.fmtQty
import com.actionaura.retail.ui.i18n.money
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.pos.GroupProblem
import com.actionaura.retail.ui.pos.ModifierPick
import com.actionaura.retail.ui.pos.chosenOptions
import com.actionaura.retail.ui.pos.defaultSelection
import com.actionaura.retail.ui.pos.groupMax
import com.actionaura.retail.ui.pos.groupMin
import com.actionaura.retail.ui.pos.isChooseOne
import com.actionaura.retail.ui.pos.isRequired
import com.actionaura.retail.ui.pos.selectionProblems
import com.actionaura.retail.ui.pos.toggleOption
import com.actionaura.retail.ui.theme.Danger

// The two pickers the till opens BEFORE a product reaches the cart (ANDROID-3): which variant of a
// product, and which extras. Neither prices anything; they collect ids, and the server prices.

/** Pick one variant of a parent product (size, colour...). `variants == null` = still loading. */
@Composable
internal fun VariantPickerSheet(
    parentName: String,
    variants: List<Product>?,
    onPick: (Product) -> Unit,
    onOutOfStock: (Product) -> Unit,
    onDismiss: () -> Unit,
) {
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(Modifier.padding(horizontal = 20.dp).padding(bottom = 24.dp).verticalScroll(rememberScrollState())) {
            Text(tr("Choose a variant") + " — " + parentName, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(12.dp))
            when {
                variants == null -> Text(tr("Loading…"), color = MaterialTheme.colorScheme.onSurfaceVariant)
                variants.isEmpty() -> Text(tr("No variants found."), color = MaterialTheme.colorScheme.onSurfaceVariant)
                else -> variants.forEach { v ->
                    val out = v.total_stock <= 0.0
                    Row(
                        Modifier.fillMaxWidth().heightIn(min = 56.dp)
                            .clickable(role = Role.Button) { if (out) onOutOfStock(v) else onPick(v) }
                            .padding(vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(v.variant_label?.takeIf { it.isNotBlank() } ?: v.name ?: "—", fontWeight = FontWeight.Medium,
                            color = if (out) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface,
                            modifier = Modifier.weight(1f))
                        Column(horizontalAlignment = Alignment.End) {
                            Text(money(v.sell_price), fontWeight = FontWeight.Bold)
                            Text(if (out) tr("Out of stock") else fmtQty(v.total_stock),
                                style = MaterialTheme.typography.labelMedium,
                                color = if (out) Danger else MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                    HorizontalDivider()
                }
            }
        }
    }
}

/**
 * Choose the extras for a product: required and optional groups, a minimum and a maximum per group.
 * The rules are in ui/pos/ModifierSelection.kt; this draws them. The sheet hands back the option ids
 * (what the sale sends as `modifier_option_ids`) and the picks (what the cart line shows).
 */
@Composable
internal fun ModifierSheet(
    product: Product,
    groups: List<ModifierGroup>,
    onAdd: (optionIds: List<String>, picks: List<ModifierPick>) -> Unit,
    onDismiss: () -> Unit,
) {
    var selected by remember { mutableStateOf(defaultSelection(groups)) }
    val problems = selectionProblems(groups, selected)
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(Modifier.padding(horizontal = 20.dp).padding(bottom = 24.dp).verticalScroll(rememberScrollState())) {
            Text(tr("Choose options") + " — " + (product.name ?: "—"), style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))
            groups.forEach { g ->
                val one = isChooseOne(g)
                Text(g.name + " · " + tr(if (isRequired(g)) "Required" else "Optional"),
                    style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 10.dp))
                val hint = when {
                    isRequired(g) && one -> tr("Choose one")
                    isRequired(g) -> tr("Choose at least") + " ${groupMin(g)}"
                    one -> ""
                    groupMax(g) != null -> tr("Choose up to") + " ${groupMax(g)}"
                    else -> tr("Choose any")
                }
                if (hint.isNotEmpty()) Text(hint, style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                g.options.forEach { o ->
                    val on = o.id in selected
                    Row(
                        Modifier.fillMaxWidth().heightIn(min = 48.dp)
                            .selectable(selected = on, role = if (one) Role.RadioButton else Role.Checkbox) {
                                selected = toggleOption(groups, selected, g.id, o.id)
                            },
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        if (one) RadioButton(selected = on, onClick = null) else Checkbox(checked = on, onCheckedChange = null)
                        Text(o.name, modifier = Modifier.weight(1f).padding(start = 8.dp))
                        if (o.price_delta != 0.0) Text(
                            (if (o.price_delta > 0) "+" else "−") + money(kotlin.math.abs(o.price_delta)),
                            style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            if (problems.isNotEmpty()) {
                Spacer(Modifier.height(10.dp))
                problems.forEach { p ->
                    Text(
                        when (p) {
                            is GroupProblem.TooFew -> p.groupName + ": " + (if (p.min == 1) tr("Choose one") else tr("Choose at least") + " ${p.min}")
                            is GroupProblem.TooMany -> p.groupName + ": " + tr("Choose up to") + " ${p.max}"
                        },
                        style = MaterialTheme.typography.bodySmall, color = Danger)
                }
            }
            Spacer(Modifier.height(14.dp))
            Button(
                onClick = { val (ids, picks) = chosenOptions(groups, selected); onAdd(ids, picks) },
                enabled = problems.isEmpty(),
                modifier = Modifier.fillMaxWidth().height(52.dp),
            ) { Text(tr("Add to cart")) }
        }
    }
}
