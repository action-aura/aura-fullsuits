@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.clinic.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ReceiptLong
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.actionaura.clinic.net.ApiClient
import com.actionaura.clinic.net.Invoice
import com.actionaura.clinic.ui.components.EmptyState
import com.actionaura.clinic.ui.components.SkeletonList
import com.actionaura.clinic.ui.i18n.tr
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BillingScreen(snackbar: SnackbarHostState) {
    // Phase 4M: invoice list is patient-linked financial data -- excluded
    // from screenshots/recent-apps while visible.
    com.actionaura.clinic.ui.components.SecureScreen()
    var items by remember { mutableStateOf<List<Invoice>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var refreshing by remember { mutableStateOf(false) }
    var showInvoice by remember { mutableStateOf(false) }
    var payInvoice by remember { mutableStateOf<Invoice?>(null) }
    // Wave 1A follow-up: invoice cards showed a summary only, with no way to
    // drill into line items or payment history even though the backend's
    // GET /invoices/{id} already returns both -- found live on a real device
    // when a tester tried to inspect an invoice they'd just paid.
    var detailInvoice by remember { mutableStateOf<Invoice?>(null) }
    val scope = rememberCoroutineScope()

    suspend fun load() { items = try { ApiClient.get().invoices().data } catch (e: Exception) { emptyList() } }
    LaunchedEffect(Unit) { loading = true; load(); loading = false }

    Box(Modifier.fillMaxSize()) {
        PullToRefreshBox(
            isRefreshing = refreshing,
            onRefresh = { scope.launch { refreshing = true; load(); refreshing = false } },
            modifier = Modifier.fillMaxSize(),
        ) {
            if (loading && items.isEmpty()) {
                SkeletonList(count = 6, modifier = Modifier.fillMaxSize())
            } else if (items.isEmpty()) {
                EmptyState(
                    icon = Icons.Default.ReceiptLong,
                    title = tr("No invoices yet"),
                    subtitle = tr("Create an invoice to start billing patients."),
                    ctaText = tr("New Invoice"), onCta = { showInvoice = true },
                )
            } else {
                LazyColumn(contentPadding = PaddingValues(16.dp, 12.dp, 16.dp, 96.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    items(items, key = { it.id }) { inv ->
                        ElevatedCard(
                            Modifier.fillMaxWidth().clickable { detailInvoice = inv },
                        ) {
                            Column(Modifier.padding(16.dp)) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text(inv.invoice_number ?: tr("Invoice"), style = MaterialTheme.typography.titleMedium,
                                        fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                                    StatusChip(inv.status ?: "unpaid")
                                }
                                Spacer(Modifier.height(6.dp))
                                Text(inv.patient_name ?: "—", style = MaterialTheme.typography.bodyMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                                Spacer(Modifier.height(8.dp))
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text(tr("Total") + " $%.2f".format(inv.total), fontWeight = FontWeight.SemiBold,
                                        modifier = Modifier.weight(1f))
                                    Text(tr("Paid") + " $%.2f".format(inv.amount_paid),
                                        color = MaterialTheme.colorScheme.primary)
                                }
                                if ((inv.status ?: "") != "paid") {
                                    Spacer(Modifier.height(10.dp))
                                    FilledTonalButton(onClick = { payInvoice = inv }) { Text(tr("Record Payment")) }
                                }
                            }
                        }
                    }
                }
            }
        }
        ExtendedFloatingActionButton(
            onClick = { showInvoice = true },
            icon = { Icon(Icons.Default.Add, null) }, text = { Text(tr("New Invoice")) },
            modifier = Modifier.align(Alignment.BottomEnd).padding(20.dp),
        )
    }

    if (showInvoice) {
        NewInvoiceSheet(
            onDismiss = { showInvoice = false },
            onCreated = { showInvoice = false; scope.launch { snackbar.showSnackbar(tr("Invoice created")); load() } },
        )
    }
    payInvoice?.let { inv ->
        PaymentSheet(
            invoice = inv,
            onDismiss = { payInvoice = null },
            onPaid = { payInvoice = null; scope.launch { snackbar.showSnackbar(tr("Payment recorded")); load() } },
        )
    }
    detailInvoice?.let { inv ->
        InvoiceDetailSheet(
            invoice = inv,
            onDismiss = { detailInvoice = null },
            onRecordPayment = { detailInvoice = null; payInvoice = inv },
        )
    }
}

@Composable
private fun InvoiceDetailSheet(invoice: Invoice, onDismiss: () -> Unit, onRecordPayment: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var detail by remember { mutableStateOf<com.actionaura.clinic.net.InvoiceDetail?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(invoice.id) {
        loading = true
        try { detail = ApiClient.get().invoiceDetail(invoice.id).data }
        catch (e: Exception) { error = com.actionaura.clinic.net.paymentErrorMessage(e) }
        loading = false
    }

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(Modifier.padding(20.dp).padding(bottom = 24.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(invoice.invoice_number ?: tr("Invoice"), style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                StatusChip(invoice.status ?: "unpaid")
            }
            Text(invoice.patient_name ?: "—", style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(16.dp))

            when {
                loading -> Box(Modifier.fillMaxWidth().padding(24.dp), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(Modifier.size(28.dp))
                }
                error != null -> Text(error!!, color = MaterialTheme.colorScheme.error)
                else -> {
                    Text(tr("Items"), style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(6.dp))
                    detail?.items?.forEach { it ->
                        Row(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                            Text("${it.description ?: "—"} × ${it.qty}", modifier = Modifier.weight(1f))
                            Text("$%.2f".format(it.line_total))
                        }
                    }
                    if (detail?.items.isNullOrEmpty()) Text(tr("No line items"), color = MaterialTheme.colorScheme.onSurfaceVariant)

                    Spacer(Modifier.height(16.dp))
                    HorizontalDivider()
                    Spacer(Modifier.height(12.dp))
                    Row(Modifier.fillMaxWidth()) {
                        Text(tr("Total"), fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                        Text("$%.2f".format(invoice.total), fontWeight = FontWeight.Bold)
                    }
                    Row(Modifier.fillMaxWidth()) {
                        Text(tr("Paid"), modifier = Modifier.weight(1f))
                        Text("$%.2f".format(invoice.amount_paid), color = MaterialTheme.colorScheme.primary)
                    }
                    Row(Modifier.fillMaxWidth()) {
                        Text(tr("Balance due"), modifier = Modifier.weight(1f))
                        Text("$%.2f".format((invoice.total - invoice.amount_paid).coerceAtLeast(0.0)))
                    }

                    Spacer(Modifier.height(16.dp))
                    Text(tr("Payment history"), style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(6.dp))
                    detail?.payments?.forEach { p ->
                        Row(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                            Text("${p.method ?: "cash"}   ${p.created_at ?: ""}", modifier = Modifier.weight(1f),
                                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            Text("$%.2f".format(p.amount_paid))
                        }
                    }
                    if (detail?.payments.isNullOrEmpty()) Text(tr("No payments recorded"), color = MaterialTheme.colorScheme.onSurfaceVariant)

                    if ((invoice.status ?: "") != "paid") {
                        Spacer(Modifier.height(20.dp))
                        Button(onClick = onRecordPayment, modifier = Modifier.fillMaxWidth().height(52.dp)) {
                            Text(tr("Record Payment"))
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun NewInvoiceSheet(onDismiss: () -> Unit, onCreated: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var patients by remember { mutableStateOf<List<com.actionaura.clinic.net.Patient>>(emptyList()) }
    var patientId by remember { mutableStateOf<Int?>(null) }
    var desc by remember { mutableStateOf("") }
    var qty by remember { mutableStateOf("1") }
    var price by remember { mutableStateOf("") }
    var discount by remember { mutableStateOf("0") }
    var taxPct by remember { mutableStateOf("0") }
    var saving by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(Unit) { patients = try { ApiClient.get().patients().data } catch (e: Exception) { emptyList() } }

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(Modifier.padding(20.dp).padding(bottom = 24.dp)) {
            Text(tr("New Invoice"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(16.dp))
            LabeledDropdown(tr("Patient"), patients.map { it.id to (it.name ?: "#${it.id}") }, patientId) { patientId = it }
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(desc, { desc = it }, label = { Text(tr("Service / item *")) },
                singleLine = true, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(qty, { qty = it }, label = { Text(tr("Qty")) }, singleLine = true, modifier = Modifier.weight(1f))
                OutlinedTextField(price, { price = it }, label = { Text(tr("Unit price")) }, singleLine = true, modifier = Modifier.weight(1f))
            }
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(discount, { discount = it }, label = { Text(tr("Discount") + " $") }, singleLine = true, modifier = Modifier.weight(1f))
                OutlinedTextField(taxPct, { taxPct = it }, label = { Text(tr("Tax") + " %") }, singleLine = true, modifier = Modifier.weight(1f))
            }
            if (error != null) { Spacer(Modifier.height(10.dp)); Text(error!!, color = MaterialTheme.colorScheme.error) }
            Spacer(Modifier.height(20.dp))
            Button(onClick = {
                val pr = price.toDoubleOrNull()
                if (patientId == null || desc.isBlank() || pr == null) { error = tr("Patient, item and price required"); return@Button }
                saving = true; error = null
                scope.launch {
                    try {
                        val r = ApiClient.get().createInvoice(com.actionaura.clinic.net.CreateInvoiceRequest(
                            patient_id = patientId!!,
                            items = listOf(com.actionaura.clinic.net.InvoiceItemReq(desc.trim(), qty.toDoubleOrNull() ?: 1.0, pr)),
                            discount = discount.toDoubleOrNull() ?: 0.0,
                            tax_rate = (taxPct.toDoubleOrNull() ?: 0.0) / 100.0))
                        if (r.status == "success") onCreated() else error = r.message ?: tr("Couldn't create")
                    } catch (e: Exception) { error = tr("Couldn't reach the server") } finally { saving = false }
                }
            }, enabled = !saving, modifier = Modifier.fillMaxWidth().height(52.dp)) {
                if (saving) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.onPrimary)
                else Text(tr("Create Invoice"), style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}

@Composable
private fun PaymentSheet(invoice: Invoice, onDismiss: () -> Unit, onPaid: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val due = (invoice.total - invoice.amount_paid).coerceAtLeast(0.0)
    var amount by remember { mutableStateOf("%.2f".format(due)) }
    var method by remember { mutableStateOf("cash") }
    var saving by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(Modifier.padding(20.dp).padding(bottom = 24.dp)) {
            Text(tr("Record Payment"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Text("${invoice.invoice_number ?: ""} · " + tr("due") + " $%.2f".format(due),
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(16.dp))
            OutlinedTextField(amount, { amount = it }, label = { Text(tr("Amount")) }, singleLine = true,
                modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(12.dp))
            LabeledDropdown(tr("Method"), listOf(0 to tr("cash"), 1 to tr("card"), 2 to tr("insurance"), 3 to tr("bank_transfer")),
                when (method) { "card" -> 1; "insurance" -> 2; "bank_transfer" -> 3; else -> 0 }) {
                method = listOf("cash", "card", "insurance", "bank_transfer")[it]
            }
            if (error != null) {
                Spacer(Modifier.height(10.dp))
                Text(error!!, color = MaterialTheme.colorScheme.error)
            }
            Spacer(Modifier.height(20.dp))
            Button(onClick = {
                val amt = amount.toDoubleOrNull() ?: return@Button
                saving = true; error = null
                scope.launch {
                    try {
                        // Commercial intent only (invoice_id, amount, method,
                        // idempotency_key) -- the server is the sole authority
                        // on whether this amount is accepted (Wave 0,
                        // AUDIT-011/012: rejects <= 0 and any overpayment
                        // beyond the outstanding balance). The response's
                        // own outstanding_balance/invoice_status/total_paid
                        // are what should ultimately be displayed, not a
                        // locally-computed equivalent -- onPaid() triggers
                        // the caller to reload the invoice from the server.
                        val r = ApiClient.get().createPayment(com.actionaura.clinic.net.CreatePaymentRequest(
                            invoice_id = invoice.id, amount = amt, method = method,
                            idempotency_key = java.util.UUID.randomUUID().toString()))
                        if (r.status == "success") onPaid()
                        else { error = r.message ?: tr("Payment could not be recorded."); saving = false }
                    } catch (e: Exception) {
                        // MOB-003: real backend rejections (400/404/500) arrive here as
                        // HttpException, not as an r.status=="error" response above --
                        // Retrofit throws for any non-2xx status on a plain-body suspend
                        // function. paymentErrorMessage() classifies the real reason
                        // (zero/negative amount, malformed amount, overpayment, missing
                        // invoice, server error, network failure) instead of always
                        // showing a generic message for what is often a correct rejection.
                        error = com.actionaura.clinic.net.paymentErrorMessage(e)
                        saving = false
                    }
                }
            }, enabled = !saving, modifier = Modifier.fillMaxWidth().height(52.dp)) {
                if (saving) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.onPrimary)
                else Text(tr("Record Payment"), style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}
