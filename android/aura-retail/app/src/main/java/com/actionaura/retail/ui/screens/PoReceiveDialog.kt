package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.*
import com.actionaura.retail.ui.i18n.fmtQty
import com.actionaura.retail.ui.i18n.ltrIsolate
import com.actionaura.retail.ui.i18n.parseNum
import com.actionaura.retail.ui.i18n.tr
import kotlinx.coroutines.launch
import java.util.UUID

// ANDROID-1007: partial purchase-order receiving.
//
// Why a separate file: BranchPinWiringContractTest / HubPairingWiringContractTest
// slice RetailExtraScreens.kt before `fun PurchaseOrdersScreen(`, so new screens
// live beside it rather than inside it.

/** What is still on order for [line]; never negative (an over-received row reads 0). */
internal fun poRemainingQty(line: PoLine): Double = maxOf(0.0, line.quantity - line.received_qty)

// Quantities are compared with a tolerance so 2.0 - 1.999999 never reads as "still owed".
private const val PO_QTY_EPSILON = 1e-6

internal sealed interface PartialReceive {
    /** Nothing to send: every typed quantity was blank or zero. */
    data object NothingEntered : PartialReceive
    /** A line was typed above what is still on order (or not a number / negative). */
    data class Invalid(val lineId: Int, val overRemaining: Boolean) : PartialReceive
    data class Ready(val request: PoReceiveRequest) : PartialReceive
}

/**
 * Turns what the cashier typed into the request the server expects: one entry
 * per line with a positive quantity (zero and blank lines are OMITTED, the
 * server treats an omitted line as "not received now"), refusing client-side
 * anything above the line's remaining. The server re-checks (OVER_RECEIPT).
 */
internal fun buildPartialReceive(lines: List<PoLine>, typed: Map<Int, String>, receiptKey: String): PartialReceive {
    val out = mutableListOf<PoReceiveLine>()
    for (l in lines) {
        val raw = typed[l.id]
        if (raw.isNullOrBlank()) continue
        val q = parseNum(raw) ?: return PartialReceive.Invalid(l.id, overRemaining = false)
        if (q < 0) return PartialReceive.Invalid(l.id, overRemaining = false)
        if (q <= PO_QTY_EPSILON) continue
        if (q > poRemainingQty(l) + PO_QTY_EPSILON) return PartialReceive.Invalid(l.id, overRemaining = true)
        out.add(PoReceiveLine(item_id = l.id, quantity = q))
    }
    if (out.isEmpty()) return PartialReceive.NothingEntered
    return PartialReceive.Ready(PoReceiveRequest(lines = out, receipt_key = receiptKey))
}

/** Receive-everything-remaining: `lines` omitted on the wire, key still sent. */
internal fun receiveAllRequest(receiptKey: String) = PoReceiveRequest(lines = null, receipt_key = receiptKey)

@Composable
internal fun PoReceiveDialog(
    poId: Int,
    lines: List<PoLine>,
    onDone: () -> Unit,
    onDismiss: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    // Minted ONCE per dialog open and reused by every retry / double-tap, so a
    // repeat of a receipt the server already wrote is answered "already
    // recorded" instead of adding the stock twice. A new open is a new receipt.
    val receiptKey = remember(poId) { UUID.randomUUID().toString() }
    val open = remember(lines) { lines.filter { poRemainingQty(it) > PO_QTY_EPSILON } }
    val typed = remember(open) {
        mutableStateMapOf<Int, String>().apply { open.forEach { put(it.id, fmtQty(poRemainingQty(it))) } }
    }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    fun send(request: PoReceiveRequest) {
        if (busy) return
        busy = true; error = null
        scope.launch {
            try {
                val r = ApiClient.get().receivePurchaseOrder(poId, request)
                if (r.status == "success") onDone() else error = r.message ?: tr("Couldn't save")
            } catch (e: Exception) { error = apiErrorMessage(e) } finally { busy = false }
        }
    }

    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text(tr("Receive stock")) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                open.forEach { l ->
                    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
                        Text(l.product_name ?: tr("Item"), fontWeight = FontWeight.Medium,
                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(
                            tr("Ordered") + " " + ltrIsolate(fmtQty(l.quantity)) + " · " +
                                tr("Received") + " " + ltrIsolate(fmtQty(l.received_qty)) + " · " +
                                tr("Remaining") + " " + ltrIsolate(fmtQty(poRemainingQty(l))),
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        OutlinedTextField(
                            value = typed[l.id] ?: "", onValueChange = { typed[l.id] = it },
                            label = { Text(tr("Receive now")) }, singleLine = true, enabled = !busy,
                            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                                keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal),
                            modifier = Modifier.fillMaxWidth())
                    }
                }
                if (error != null) {
                    Spacer(Modifier.height(8.dp))
                    Text(error!!, color = MaterialTheme.colorScheme.error)
                }
            }
        },
        confirmButton = {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                TextButton(enabled = !busy, onClick = { send(receiveAllRequest(receiptKey)) }) {
                    Text(tr("Receive all"))
                }
                Button(enabled = !busy, onClick = {
                    when (val plan = buildPartialReceive(open, typed, receiptKey)) {
                        is PartialReceive.Ready -> send(plan.request)
                        PartialReceive.NothingEntered -> error = tr("Enter a quantity to receive")
                        is PartialReceive.Invalid -> error =
                            if (plan.overRemaining) tr("You cannot receive more than is still on order.")
                            else tr("Enter a valid quantity")
                    }
                }) {
                    if (busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp,
                        color = MaterialTheme.colorScheme.onPrimary)
                    else Text(tr("Receive stock"))
                }
            }
        },
        dismissButton = { TextButton(enabled = !busy, onClick = onDismiss) { Text(tr("Cancel")) } },
    )
}
