@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.SwapHoriz
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.*
import com.actionaura.retail.ui.CAPS_STOCK_WRITE
import com.actionaura.retail.ui.RetailSession
import com.actionaura.retail.ui.components.EmptyState
import com.actionaura.retail.ui.components.SkeletonList
import com.actionaura.retail.ui.components.TillCard
import com.actionaura.retail.ui.i18n.fmtQty
import com.actionaura.retail.ui.i18n.ltrIsolate
import com.actionaura.retail.ui.i18n.parseNum
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.theme.Info
import com.actionaura.retail.ui.theme.Success
import com.actionaura.retail.ui.theme.Warning
import kotlinx.coroutines.launch

// ANDROID-1007: stock transfers between branches (the desktop has had this
// since schema v28; the phone had none).
//
// Lifecycle (server-owned): pending -> (Send) in_transit -> (Receive) received;
// a pending transfer can be cancelled, an in-transit one recalled. Nothing
// moves on the stock ledger until SEND, so Create is cheap and reversible.
// Every write needs CAP_STOCK_ADJUST -- the same CAPS_STOCK_WRITE list the PO
// buttons use, pinned by WriteGateContractTest. Reads are open to any
// signed-in user. The server re-gates every call regardless.

/**
 * The receive body: EVERY line must be listed (0 allowed -- "sent but none
 * arrived" is a real answer), so a blank or unparseable field is a refusal,
 * never a silent skip. Returns null when any line is not a valid number >= 0.
 */
internal fun buildTransferReceive(items: List<TransferItem>, typed: Map<String, String>): ReceiveTransferRequest? {
    val out = ArrayList<ReceiveTransferItem>(items.size)
    for (it in items) {
        val q = parseNum(typed[it.id]) ?: return null
        if (q < 0) return null
        out.add(ReceiveTransferItem(id = it.id, quantity_received = q))
    }
    return ReceiveTransferRequest(out)
}

/** What a line was sent as; falls back to the requested quantity before Send. */
internal fun transferSentQty(item: TransferItem): Double = item.quantity_sent ?: item.quantity

private val TRANSFER_FILTERS = listOf("" to "All", "pending" to "Pending", "in_transit" to "In transit", "received" to "Received")

private fun transferStatusLabel(status: String?): String = when (status) {
    "pending" -> tr("Pending")
    "in_transit" -> tr("In transit")
    "received" -> tr("Received")
    "cancelled" -> tr("Cancelled")
    else -> (status ?: "").replaceFirstChar { it.uppercase() }
}

@Composable
private fun transferStatusColor(status: String?): Color = when (status) {
    "received" -> Success
    "pending" -> Warning
    "in_transit" -> Info
    "cancelled" -> MaterialTheme.colorScheme.error
    else -> MaterialTheme.colorScheme.onSurfaceVariant
}

@Composable
fun StockTransfersScreen(snackbar: SnackbarHostState) {
    var transfers by remember { mutableStateOf<List<Transfer>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var loadError by remember { mutableStateOf<String?>(null) }
    var filter by remember { mutableStateOf("") }
    var showCreate by remember { mutableStateOf(false) }
    var detailId by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val canWrite = RetailSession.hasAnyCapability(CAPS_STOCK_WRITE)

    suspend fun load() {
        try {
            transfers = ApiClient.get().stockTransfers(status = filter.ifEmpty { null }).data
            loadError = null
        } catch (e: Exception) { loadError = apiErrorMessage(e) }
    }
    LaunchedEffect(filter) { loading = true; load(); loading = false }

    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TRANSFER_FILTERS.forEach { (code, label) ->
                FilterChip(selected = filter == code, onClick = { filter = code }, label = { Text(tr(label)) })
            }
        }
        Box(Modifier.weight(1f).fillMaxWidth()) {
            when {
                loading -> SkeletonList()
                loadError != null -> Text(loadError!!, color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.padding(16.dp))
                transfers.isEmpty() -> EmptyState(Icons.Default.SwapHoriz, tr("No stock transfers"),
                    if (canWrite) tr("Move stock from one branch to another.") else tr("Stock transfers will show here."),
                    ctaText = if (canWrite) tr("New Transfer") else null,
                    onCta = if (canWrite) ({ showCreate = true }) else null)
                else -> LazyColumn(
                    contentPadding = PaddingValues(16.dp, 8.dp, 16.dp, 90.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) { items(transfers, key = { it.id }) { t -> TransferRow(t) { detailId = t.id } } }
            }
            if (transfers.isNotEmpty() && canWrite) ExtendedFloatingActionButton(
                onClick = { showCreate = true },
                icon = { Icon(Icons.Default.Add, null) }, text = { Text(tr("New Transfer")) },
                modifier = Modifier.align(Alignment.BottomEnd).padding(20.dp),
            )
        }
    }

    if (showCreate && canWrite) CreateTransferSheet(
        onDismiss = { showCreate = false },
        onCreated = { showCreate = false; scope.launch { load(); snackbar.showSnackbar(tr("Transfer created")) } },
    )
    if (detailId != null) TransferDetailSheet(
        transferId = detailId!!, canWrite = canWrite,
        onDismiss = { detailId = null },
        onChanged = { scope.launch { load() } },
    )
}

@Composable
private fun TransferRow(t: Transfer, onClick: () -> Unit) {
    TillCard(Modifier.fillMaxWidth(), onClick = onClick) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("${t.source_branch_name ?: "—"} → ${t.destination_branch_name ?: "—"}",
                    fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(ltrIsolate((t.created_at ?: "").replace("T", " ").take(16)),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Text(transferStatusLabel(t.status), style = MaterialTheme.typography.labelMedium,
                color = transferStatusColor(t.status), fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
private fun CreateTransferSheet(onDismiss: () -> Unit, onCreated: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    var branches by remember { mutableStateOf<List<Branch>>(emptyList()) }
    var products by remember { mutableStateOf<List<Product>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) {
        try {
            branches = ApiClient.get().branches().data.filter { (it.status ?: "active") == "active" }
            products = ApiClient.get().products().data
        } catch (e: Exception) { error = apiErrorMessage(e) }
    }
    var from by remember { mutableStateOf<Branch?>(null) }
    var to by remember { mutableStateOf<Branch?>(null) }
    var fromMenu by remember { mutableStateOf(false) }
    var toMenu by remember { mutableStateOf(false) }
    var product by remember { mutableStateOf<Product?>(null) }
    var productMenu by remember { mutableStateOf(false) }
    var qty by remember { mutableStateOf("1") }
    val lines = remember { mutableStateListOf<Pair<Product, Double>>() }
    var saving by remember { mutableStateOf(false) }

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(Modifier.padding(20.dp).padding(bottom = 24.dp).verticalScroll(rememberScrollState())) {
            Text(tr("New Transfer"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(16.dp))
            BranchChoice(tr("From branch"), from, fromMenu, { fromMenu = it }, branches) { from = it }
            Spacer(Modifier.height(8.dp))
            BranchChoice(tr("To branch"), to, toMenu, { toMenu = it }, branches) { to = it }

            Spacer(Modifier.height(16.dp))
            Text(tr("Items"), fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))
            Box {
                OutlinedButton(onClick = { productMenu = true }, modifier = Modifier.fillMaxWidth()) {
                    Text(product?.let { "${it.name} (${it.sku})" } ?: tr("Select product"),
                        modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Icon(Icons.Default.ArrowDropDown, null)
                }
                DropdownMenu(expanded = productMenu, onDismissRequest = { productMenu = false }) {
                    products.forEach { p ->
                        DropdownMenuItem(text = { Text("${p.name} (${p.sku})") },
                            onClick = { product = p; productMenu = false })
                    }
                }
            }
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(qty, { qty = it }, label = { Text(tr("Qty")) }, singleLine = true,
                    keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                        keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal),
                    modifier = Modifier.weight(1f))
                FilledTonalIconButton(onClick = {
                    val p = product; val q = parseNum(qty)
                    if (p != null && q != null && q > 0) { lines.add(p to q); product = null; qty = "1" }
                }) { Icon(Icons.Default.Add, tr("Add item")) }
            }
            lines.forEachIndexed { i, (p, q) ->
                Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("${p.name}  ×${fmtQty(q)}", Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                    TextButton(onClick = { lines.removeAt(i) }) { Text(tr("Remove")) }
                }
            }

            if (error != null) { Spacer(Modifier.height(10.dp)); Text(error!!, color = MaterialTheme.colorScheme.error) }
            Spacer(Modifier.height(20.dp))
            Button(
                onClick = {
                    val s = from; val d = to
                    if (s == null || d == null) { error = tr("Choose both branches"); return@Button }
                    if (s.id == d.id) { error = tr("The two branches must be different"); return@Button }
                    if (lines.isEmpty()) { error = tr("Add at least one item"); return@Button }
                    saving = true; error = null
                    scope.launch {
                        try {
                            val r = ApiClient.get().createStockTransfer(CreateTransferRequest(
                                source_branch_id = s.id, destination_branch_id = d.id,
                                items = lines.map { CreateTransferLine(it.first.id, it.second) }))
                            if (r.status == "success") onCreated() else error = r.message ?: tr("Couldn't save")
                        } catch (e: Exception) { error = apiErrorMessage(e) } finally { saving = false }
                    }
                },
                enabled = !saving, modifier = Modifier.fillMaxWidth().height(52.dp),
            ) {
                if (saving) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.onPrimary)
                else Text(tr("Create Transfer"), style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}

@Composable
private fun BranchChoice(
    label: String, value: Branch?, expanded: Boolean, setExpanded: (Boolean) -> Unit,
    branches: List<Branch>, onPick: (Branch) -> Unit,
) {
    Box {
        OutlinedButton(onClick = { setExpanded(true) }, modifier = Modifier.fillMaxWidth()) {
            Text(value?.name ?: label, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
            Icon(Icons.Default.ArrowDropDown, null)
        }
        DropdownMenu(expanded = expanded, onDismissRequest = { setExpanded(false) }) {
            branches.forEach { b ->
                DropdownMenuItem(text = { Text(b.name ?: "—") }, onClick = { onPick(b); setExpanded(false) })
            }
        }
    }
}

private enum class TransferAction { SEND, CANCEL, RECALL }

@Composable
private fun TransferDetailSheet(transferId: String, canWrite: Boolean, onDismiss: () -> Unit, onChanged: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    var detail by remember { mutableStateOf<TransferDetail?>(null) }
    var loading by remember { mutableStateOf(true) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var pending by remember { mutableStateOf<TransferAction?>(null) }
    var receiving by remember { mutableStateOf(false) }
    var shortages by remember { mutableStateOf<List<TransferShortage>>(emptyList()) }

    suspend fun reload() {
        try { detail = ApiClient.get().stockTransfer(transferId).data }
        catch (e: Exception) { detail = null; error = apiErrorMessage(e) }
    }
    LaunchedEffect(transferId) { reload(); loading = false }

    fun run(action: TransferAction) {
        busy = true; error = null
        scope.launch {
            try {
                val api = ApiClient.get()
                val r = when (action) {
                    TransferAction.SEND -> api.sendStockTransfer(transferId)
                    TransferAction.CANCEL -> api.cancelStockTransfer(transferId)
                    TransferAction.RECALL -> api.recallStockTransfer(transferId)
                }
                if (r.status == "success") { reload(); onChanged() } else error = r.message ?: tr("Couldn't save")
            } catch (e: Exception) { error = apiErrorMessage(e) } finally { busy = false; pending = null }
        }
    }

    pending?.let { action ->
        AlertDialog(
            onDismissRequest = { if (!busy) pending = null },
            title = { Text(when (action) {
                TransferAction.SEND -> tr("Send this transfer?")
                TransferAction.CANCEL -> tr("Cancel this transfer?")
                TransferAction.RECALL -> tr("Recall this transfer?")
            }) },
            text = { Text(when (action) {
                TransferAction.SEND -> tr("The stock leaves the source branch now.")
                TransferAction.CANCEL -> tr("Nothing has been sent, so no stock moves.")
                TransferAction.RECALL -> tr("The stock goes back to the source branch.")
            }) },
            confirmButton = { TextButton(enabled = !busy, onClick = { run(action) }) { Text(tr("Confirm")) } },
            dismissButton = { TextButton(enabled = !busy, onClick = { pending = null }) { Text(tr("Cancel")) } },
        )
    }
    if (receiving) detail?.let { d ->
        ReceiveTransferDialog(transferId, d.items,
            onDone = { s -> receiving = false; shortages = s; scope.launch { reload(); onChanged() } },
            onDismiss = { receiving = false })
    }

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(Modifier.padding(20.dp).padding(bottom = 24.dp).verticalScroll(rememberScrollState())) {
            val t = detail?.transfer
            Text("${t?.source_branch_name ?: "—"} → ${t?.destination_branch_name ?: "—"}",
                style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Text(transferStatusLabel(t?.status), color = transferStatusColor(t?.status),
                style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(16.dp))
            when {
                loading -> CircularProgressIndicator(Modifier.size(28.dp), strokeWidth = 2.dp)
                detail == null -> Text(error ?: tr("Couldn't load this transfer."), color = MaterialTheme.colorScheme.error)
                else -> {
                    detail!!.items.forEach { it ->
                        Row(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
                            Column(Modifier.weight(1f)) {
                                Text(it.product_name ?: tr("Item"), maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text(ltrIsolate(it.sku ?: ""), style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            Column(horizontalAlignment = Alignment.End) {
                                Text(tr("Sent") + " " + ltrIsolate(fmtQty(transferSentQty(it))))
                                it.quantity_received?.let { r ->
                                    Text(tr("Received") + " " + ltrIsolate(fmtQty(r)),
                                        style = MaterialTheme.typography.labelMedium,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                        }
                    }
                    if (shortages.isNotEmpty()) {
                        HorizontalDivider(Modifier.padding(vertical = 10.dp))
                        Text(tr("Came up short"), fontWeight = FontWeight.Bold, color = Warning)
                        val names = detail!!.items.associate { (it.product_id ?: "") to (it.product_name ?: tr("Item")) }
                        shortages.forEach { s ->
                            Text((names[s.product_id ?: ""] ?: tr("Item")) + ": " +
                                ltrIsolate(fmtQty(s.quantity_sent)) + " → " + ltrIsolate(fmtQty(s.quantity_received)),
                                style = MaterialTheme.typography.bodySmall)
                        }
                    }
                    if (error != null) { Spacer(Modifier.height(10.dp)); Text(error!!, color = MaterialTheme.colorScheme.error) }
                    if (canWrite) {
                        Spacer(Modifier.height(16.dp))
                        when (t?.status) {
                            "pending" -> {
                                Button(onClick = { pending = TransferAction.SEND }, enabled = !busy,
                                    modifier = Modifier.fillMaxWidth().height(52.dp)) { Text(tr("Send")) }
                                Spacer(Modifier.height(8.dp))
                                OutlinedButton(onClick = { pending = TransferAction.CANCEL }, enabled = !busy,
                                    modifier = Modifier.fillMaxWidth().height(48.dp)) { Text(tr("Cancel transfer")) }
                            }
                            "in_transit" -> {
                                Button(onClick = { error = null; receiving = true }, enabled = !busy,
                                    modifier = Modifier.fillMaxWidth().height(52.dp)) { Text(tr("Receive stock")) }
                                Spacer(Modifier.height(8.dp))
                                OutlinedButton(onClick = { pending = TransferAction.RECALL }, enabled = !busy,
                                    modifier = Modifier.fillMaxWidth().height(48.dp)) { Text(tr("Recall")) }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ReceiveTransferDialog(
    transferId: String, items: List<TransferItem>,
    onDone: (List<TransferShortage>) -> Unit, onDismiss: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    // Every line defaults to what was sent: the common case is "all of it arrived".
    val typed = remember(items) {
        mutableStateMapOf<String, String>().apply { items.forEach { put(it.id, fmtQty(transferSentQty(it))) } }
    }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text(tr("Receive stock")) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Text(tr("Enter how many of each item actually arrived. Use 0 if none did."),
                    style = MaterialTheme.typography.bodySmall)
                items.forEach { l ->
                    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
                        Text(l.product_name ?: tr("Item"), fontWeight = FontWeight.Medium,
                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                        OutlinedTextField(
                            value = typed[l.id] ?: "", onValueChange = { typed[l.id] = it },
                            label = { Text(tr("Sent") + " " + ltrIsolate(fmtQty(transferSentQty(l)))) },
                            singleLine = true, enabled = !busy,
                            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                                keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal),
                            modifier = Modifier.fillMaxWidth())
                    }
                }
                if (error != null) { Spacer(Modifier.height(8.dp)); Text(error!!, color = MaterialTheme.colorScheme.error) }
            }
        },
        confirmButton = {
            Button(enabled = !busy, onClick = {
                val body = buildTransferReceive(items, typed)
                if (body == null) { error = tr("Enter a valid quantity for every item"); return@Button }
                busy = true; error = null
                scope.launch {
                    try {
                        val r = ApiClient.get().receiveStockTransfer(transferId, body)
                        if (r.status == "success") onDone(r.data?.shortages ?: emptyList())
                        else error = r.message ?: tr("Couldn't save")
                    } catch (e: Exception) { error = apiErrorMessage(e) } finally { busy = false }
                }
            }) { Text(tr("Receive stock")) }
        },
        dismissButton = { TextButton(enabled = !busy, onClick = onDismiss) { Text(tr("Cancel")) } },
    )
}
