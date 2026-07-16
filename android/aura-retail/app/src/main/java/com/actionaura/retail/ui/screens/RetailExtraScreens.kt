@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.actionaura.retail.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountBalanceWallet
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.AssignmentReturn
import androidx.compose.material.icons.filled.BarChart
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Inventory2
import androidx.compose.material.icons.filled.LocalShipping
import androidx.compose.material.icons.filled.Payments
import androidx.compose.material.icons.filled.People
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material.icons.filled.ReceiptLong
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.*
import com.actionaura.retail.ui.components.EmptyState
import com.actionaura.retail.ui.components.GlowCard
import com.actionaura.retail.ui.components.SectionHeader
import com.actionaura.retail.ui.components.SkeletonList
import com.actionaura.retail.ui.i18n.fmtQty
import com.actionaura.retail.ui.i18n.parseNum
import com.actionaura.retail.ui.i18n.tr
import com.actionaura.retail.ui.theme.Info
import com.actionaura.retail.ui.theme.Success
import com.actionaura.retail.ui.theme.Warning
import kotlinx.coroutines.launch

private fun money(v: Double) = String.format(java.util.Locale.US, "$%.2f", v)
private fun shortDate(s: String?) = (s ?: "").replace("T", " ").take(16)

// ══════════════════════════════════════════════════════════════════════════════
//  MORE — hub linking to the records screens
// ══════════════════════════════════════════════════════════════════════════════
@Composable
fun MoreScreen(onNavigate: (String) -> Unit) {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        SectionHeader(tr("Records"))
        MoreItem(tr("Reports"), tr("Sales stats by time period"), Icons.Default.BarChart) { onNavigate("reports") }
        MoreItem(tr("Transactions"), tr("Past sales & invoices"), Icons.Default.ReceiptLong) { onNavigate("transactions") }
        MoreItem(tr("Returns"), tr("Refund items from a sale"), Icons.Default.AssignmentReturn) { onNavigate("returns") }
        SectionHeader(tr("Customers & credit"))
        MoreItem(tr("Customers"), tr("Manage customers & credit"), Icons.Default.People) { onNavigate("customers") }
        MoreItem(tr("Receivables"), tr("Who owes you & repayments"), Icons.Default.AccountBalanceWallet) { onNavigate("receivables") }
        SectionHeader(tr("Purchasing"))
        MoreItem(tr("Suppliers"), tr("Vendors you buy from"), Icons.Default.LocalShipping) { onNavigate("suppliers") }
        MoreItem(tr("Purchase Orders"), tr("Restock & receive stock"), Icons.Default.Inventory2) { onNavigate("purchase_orders") }
        MoreItem(tr("Payables"), tr("What you owe suppliers"), Icons.Default.Payments) { onNavigate("payables") }
        SectionHeader(tr("Finance"))
        MoreItem(tr("Cash Summary"), tr("Daily cash in / out / net"), Icons.Default.AccountBalanceWallet) { onNavigate("cash_summary") }
        MoreItem(tr("Aging"), tr("Receivables & payables by age"), Icons.Default.Schedule) { onNavigate("aging") }
        MoreItem(tr("Settings"), tr("Credit, currency & payment methods"), Icons.Default.Settings) { onNavigate("retail_settings") }
    }
}

@Composable
private fun MoreItem(title: String, subtitle: String, icon: ImageVector, onClick: () -> Unit) {
    GlowCard(Modifier.fillMaxWidth(), onClick = onClick) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(icon, null, tint = MaterialTheme.colorScheme.primary)
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Text(title, fontWeight = FontWeight.Bold)
                Text(subtitle, style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Icon(Icons.Default.ChevronRight, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

// ══════════════════════════════════════════════════════════════════════════════
//  REPORTS — period-selectable stats (Today / 7d / 30d / 90d / 1y)
// ══════════════════════════════════════════════════════════════════════════════
private data class ReportPeriod(val label: String, val days: Int)
private val reportPeriods = listOf(
    ReportPeriod("Today", 0), ReportPeriod("7 days", 7), ReportPeriod("30 days", 30),
    ReportPeriod("90 days", 90), ReportPeriod("1 year", 365),
)

@Composable
fun ReportsScreen(snackbar: SnackbarHostState) {
    var days by remember { mutableStateOf(30) }
    var summary by remember { mutableStateOf<ReportSummary?>(null) }
    var payments by remember { mutableStateOf<List<PaymentMethodStat>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }

    // Refetch whenever the chosen period changes (or the screen first loads).
    LaunchedEffect(days) {
        loading = true
        summary = try { ApiClient.get().reportSummary(days).data } catch (e: Exception) { null }
        payments = try { ApiClient.get().reportPaymentMethods(days).data } catch (e: Exception) { emptyList() }
        loading = false
    }

    val s = summary ?: ReportSummary()
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            items(reportPeriods) { p ->
                FilterChip(selected = days == p.days, onClick = { days = p.days }, label = { Text(tr(p.label)) })
            }
        }

        if (loading && summary == null) {
            Box(Modifier.fillMaxWidth().padding(40.dp), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
        } else {
            val change = s.revenue_change
            val changeText = when {
                change > 0 -> "▲ $change% " + tr("vs previous period")
                change < 0 -> "▼ ${kotlin.math.abs(change)}% " + tr("vs previous period")
                else -> tr("vs previous period")
            }
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                StatCard(tr("Revenue"), money(s.revenue), changeText, Success, Modifier.weight(1f))
                StatCard(tr("Transactions"), s.transactions.toString(), tr("sales"), Info, Modifier.weight(1f))
            }
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                StatCard(tr("Gross Profit"), money(s.gross_profit), "${s.margin_pct}% " + tr("margin"),
                    MaterialTheme.colorScheme.primary, Modifier.weight(1f))
                StatCard(tr("Avg Ticket"), money(s.avg_ticket), tr("per sale"), Warning, Modifier.weight(1f))
            }
            StatCard(tr("Inventory Value (at cost)"), money(s.inventory_value), tr("current stock on hand"),
                Info, Modifier.fillMaxWidth())

            if (payments.isNotEmpty()) {
                SectionHeader(tr("Payment methods"))
                GlowCard(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        payments.forEach { pm ->
                            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                Text((pm.payment_method ?: "—").replaceFirstChar { it.uppercase() }, Modifier.weight(1f))
                                Text("${pm.count}×", style = MaterialTheme.typography.labelMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                                Spacer(Modifier.width(12.dp))
                                Text(money(pm.revenue), fontWeight = FontWeight.Bold)
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun StatCard(label: String, value: String, sub: String?, accent: Color, modifier: Modifier = Modifier) {
    GlowCard(modifier = modifier, glow = accent) {
        Column(Modifier.padding(16.dp)) {
            Text(label.uppercase(), style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(8.dp))
            Text(value, style = MaterialTheme.typography.headlineSmall, color = accent, fontWeight = FontWeight.ExtraBold)
            if (sub != null) {
                Spacer(Modifier.height(2.dp))
                Text(sub, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

// ══════════════════════════════════════════════════════════════════════════════
//  TRANSACTIONS — sales history + detail
// ══════════════════════════════════════════════════════════════════════════════
@Composable
fun TransactionsScreen(snackbar: SnackbarHostState) {
    var sales by remember { mutableStateOf<List<Sale>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var detailId by remember { mutableStateOf<Int?>(null) }

    LaunchedEffect(Unit) {
        sales = try { ApiClient.get().recentSales(100).data } catch (e: Exception) { emptyList() }
        loading = false
    }

    when {
        loading -> SkeletonList()
        sales.isEmpty() -> EmptyState(Icons.Default.ReceiptLong, tr("No transactions yet"),
            tr("Sales you complete in POS will show up here as invoices."))
        else -> LazyColumn(
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            items(sales, key = { it.id }) { s -> SaleRow(s) { detailId = s.id } }
        }
    }

    if (detailId != null) SaleDetailSheet(detailId!!, onDismiss = { detailId = null })
}

@Composable
private fun SaleRow(s: Sale, onClick: () -> Unit) {
    GlowCard(Modifier.fillMaxWidth(), onClick = onClick) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(s.sale_number ?: "—", fontWeight = FontWeight.Bold)
                Text("${s.customer_name ?: tr("Walk-in")} · " + tr("%d item(s)").format(s.item_count) + " · ${s.payment_method ?: "cash"}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(shortDate(s.created_at), style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Text(money(s.total), fontWeight = FontWeight.ExtraBold, color = Success,
                style = MaterialTheme.typography.titleMedium)
        }
    }
}

@Composable
private fun SaleDetailSheet(saleId: Int, onDismiss: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var detail by remember { mutableStateOf<SaleDetail?>(null) }
    var loading by remember { mutableStateOf(true) }
    LaunchedEffect(saleId) {
        detail = try { ApiClient.get().saleDetail(saleId).data } catch (e: Exception) { null }
        loading = false
    }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(Modifier.padding(20.dp).padding(bottom = 24.dp).verticalScroll(rememberScrollState())) {
            val s = detail?.sale
            Text(s?.sale_number ?: tr("Receipt"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Text("${s?.customer_name ?: tr("Walk-in")} · ${shortDate(s?.created_at)}",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(16.dp))
            when {
                loading -> CircularProgressIndicator(Modifier.size(28.dp), strokeWidth = 2.dp)
                detail == null -> Text(tr("Couldn't load this transaction."), color = MaterialTheme.colorScheme.error)
                else -> {
                    detail!!.items.forEach { it ->
                        Row(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
                            Column(Modifier.weight(1f)) {
                                Text(it.product_name ?: tr("Item"), maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text("${fmtQty(it.quantity)} × ${money(it.unit_price)}",
                                    style = MaterialTheme.typography.labelMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            Text(money(it.line_total), fontWeight = FontWeight.Medium)
                        }
                    }
                    HorizontalDivider(Modifier.padding(vertical = 10.dp))
                    SummaryRow(tr("Subtotal"), money(s?.subtotal ?: 0.0))
                    if ((s?.discount_amount ?: 0.0) > 0) SummaryRow(tr("Discount"), "-" + money(s?.discount_amount ?: 0.0))
                    if ((s?.tax_amount ?: 0.0) > 0) SummaryRow(tr("Tax"), money(s?.tax_amount ?: 0.0))
                    SummaryRow(tr("Total"), money(s?.total ?: 0.0), bold = true)
                    SummaryRow(tr("Paid") + " (${s?.payment_method ?: "cash"})", money(s?.amount_paid ?: 0.0))
                    if ((s?.change_amount ?: 0.0) > 0) SummaryRow(tr("Change"), money(s?.change_amount ?: 0.0))
                }
            }
        }
    }
}

@Composable
private fun SummaryRow(label: String, value: String, bold: Boolean = false) {
    Row(Modifier.fillMaxWidth().padding(vertical = 3.dp)) {
        Text(label, Modifier.weight(1f),
            color = if (bold) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
            fontWeight = if (bold) FontWeight.Bold else FontWeight.Normal)
        Text(value, fontWeight = if (bold) FontWeight.ExtraBold else FontWeight.Medium)
    }
}

// ══════════════════════════════════════════════════════════════════════════════
//  RETURNS — refund items from a past sale (auto-restocks)
// ══════════════════════════════════════════════════════════════════════════════
@Composable
fun ReturnsScreen(snackbar: SnackbarHostState) {
    var rows by remember { mutableStateOf<List<Return>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var showNew by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    suspend fun load() { rows = try { ApiClient.get().returns().data } catch (e: Exception) { emptyList() } }
    LaunchedEffect(Unit) { load(); loading = false }

    Box(Modifier.fillMaxSize()) {
        when {
            loading -> SkeletonList()
            rows.isEmpty() -> EmptyState(Icons.Default.AssignmentReturn, tr("No returns yet"),
                tr("Refund items from a past sale; stock is added back automatically."),
                ctaText = tr("Process return"), onCta = { showNew = true })
            else -> LazyColumn(
                contentPadding = PaddingValues(16.dp, 16.dp, 16.dp, 90.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) { items(rows, key = { it.id }) { r -> ReturnRow(r) } }
        }
        if (rows.isNotEmpty()) ExtendedFloatingActionButton(
            onClick = { showNew = true },
            icon = { Icon(Icons.Default.Add, null) }, text = { Text(tr("Process return")) },
            modifier = Modifier.align(Alignment.BottomEnd).padding(20.dp),
        )
    }

    if (showNew) ProcessReturnSheet(
        onDismiss = { showNew = false },
        onDone = { showNew = false; scope.launch { loading = true; load(); loading = false; snackbar.showSnackbar(tr("Return processed")) } },
    )
}

@Composable
private fun ReturnRow(r: Return) {
    GlowCard(Modifier.fillMaxWidth()) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(r.return_number ?: "—", fontWeight = FontWeight.Bold)
                Text("${r.sale_number ?: "—"} · ${tr(r.reason ?: "")}", style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(shortDate(r.created_at), style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Column(horizontalAlignment = Alignment.End) {
                Text("-" + money(r.refund_amount), fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.error)
                Text(tr((r.refund_method ?: "cash").replaceFirstChar { it.uppercase() }),
                    style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
private fun ProcessReturnSheet(onDismiss: () -> Unit, onDone: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    var sales by remember { mutableStateOf<List<Sale>>(emptyList()) }
    LaunchedEffect(Unit) { sales = try { ApiClient.get().recentSales(100).data } catch (e: Exception) { emptyList() } }

    var sale by remember { mutableStateOf<Sale?>(null) }
    var saleMenu by remember { mutableStateOf(false) }
    var lines by remember { mutableStateOf<List<SaleLine>>(emptyList()) }
    // qty to return per line index (as a string so it's directly editable). Absent/blank = 0.
    val retQty = remember { mutableStateMapOf<Int, String>() }
    var reason by remember { mutableStateOf("Customer return") }
    var reasonMenu by remember { mutableStateOf(false) }
    var refund by remember { mutableStateOf("cash") }
    var refundMenu by remember { mutableStateOf(false) }
    var saving by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf<String?>(null) }

    val reasons = listOf("Customer return", "Defective / damaged", "Wrong item", "Not as described", "Changed mind")
    val refunds = listOf("cash", "card", "store_credit")

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(Modifier.padding(20.dp).padding(bottom = 24.dp).verticalScroll(rememberScrollState())) {
            Text(tr("Process Return"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(16.dp))

            Box {
                OutlinedButton(onClick = { saleMenu = true }, modifier = Modifier.fillMaxWidth()) {
                    Text(sale?.sale_number ?: tr("Select a receipt"), modifier = Modifier.weight(1f),
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Icon(Icons.Default.ArrowDropDown, null)
                }
                DropdownMenu(expanded = saleMenu, onDismissRequest = { saleMenu = false }) {
                    if (sales.isEmpty()) DropdownMenuItem(text = { Text(tr("No sales found")) }, onClick = { saleMenu = false })
                    sales.forEach { s ->
                        DropdownMenuItem(
                            text = { Text("${s.sale_number} · ${money(s.total)}") },
                            onClick = {
                                sale = s; retQty.clear(); lines = emptyList(); saleMenu = false
                                scope.launch {
                                    lines = try { ApiClient.get().saleDetail(s.id).data?.items ?: emptyList() }
                                            catch (e: Exception) { emptyList() }
                                }
                            },
                        )
                    }
                }
            }

            if (lines.isNotEmpty()) {
                Spacer(Modifier.height(16.dp))
                Text(tr("Choose quantity to return"), fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(4.dp))
                var refundTotal = 0.0
                lines.forEachIndexed { i, ln ->
                    val cur = (parseNum(retQty[i]) ?: 0.0).coerceIn(0.0, ln.quantity)
                    refundTotal += cur * ln.unit_price
                    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(ln.product_name ?: tr("Item"), maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(tr("Sold") + " ${fmtQty(ln.quantity)} × ${money(ln.unit_price)}",
                                style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        // − / qty / +  bounded to [0, quantity sold on this line]
                        FilledTonalIconButton(
                            onClick = { retQty[i] = fmtQty((cur - 1).coerceAtLeast(0.0)) },
                            enabled = cur > 0.0, modifier = Modifier.size(34.dp),
                        ) { Icon(Icons.Default.Remove, tr("Remove")) }
                        OutlinedTextField(
                            value = retQty[i] ?: "",
                            onValueChange = { v ->
                                // keep what they type; clamp to the sold qty so they can't over-return
                                val n = parseNum(v)
                                retQty[i] = if (n != null && n > ln.quantity) fmtQty(ln.quantity) else v
                            },
                            placeholder = { Text("0") }, singleLine = true,
                            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                                keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal),
                            modifier = Modifier.width(72.dp),
                            textStyle = androidx.compose.ui.text.TextStyle(textAlign = androidx.compose.ui.text.style.TextAlign.Center),
                        )
                        FilledTonalIconButton(
                            onClick = { retQty[i] = fmtQty((cur + 1).coerceAtMost(ln.quantity)) },
                            enabled = cur < ln.quantity, modifier = Modifier.size(34.dp),
                        ) { Icon(Icons.Default.Add, tr("Add")) }
                    }
                }
                Spacer(Modifier.height(8.dp))
                Row(Modifier.fillMaxWidth()) {
                    Text(tr("Refund total"), Modifier.weight(1f), fontWeight = FontWeight.Bold)
                    Text(money(refundTotal), fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.error)
                }

                Spacer(Modifier.height(16.dp))
                Text(tr("Reason"), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Box {
                    OutlinedButton(onClick = { reasonMenu = true }, modifier = Modifier.fillMaxWidth()) {
                        Text(tr(reason), modifier = Modifier.weight(1f)); Icon(Icons.Default.ArrowDropDown, null)
                    }
                    DropdownMenu(expanded = reasonMenu, onDismissRequest = { reasonMenu = false }) {
                        reasons.forEach { x -> DropdownMenuItem(text = { Text(tr(x)) }, onClick = { reason = x; reasonMenu = false }) }
                    }
                }
                Spacer(Modifier.height(8.dp))
                Text(tr("Refund method"), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Box {
                    OutlinedButton(onClick = { refundMenu = true }, modifier = Modifier.fillMaxWidth()) {
                        Text(tr(refund.replaceFirstChar { it.uppercase() }), modifier = Modifier.weight(1f))
                        Icon(Icons.Default.ArrowDropDown, null)
                    }
                    DropdownMenu(expanded = refundMenu, onDismissRequest = { refundMenu = false }) {
                        refunds.forEach { x ->
                            DropdownMenuItem(text = { Text(tr(x.replaceFirstChar { it.uppercase() })) },
                                onClick = { refund = x; refundMenu = false })
                        }
                    }
                }

                if (err != null) { Spacer(Modifier.height(10.dp)); Text(err!!, color = MaterialTheme.colorScheme.error) }
                Spacer(Modifier.height(20.dp))
                Button(
                    onClick = {
                        val s = sale ?: return@Button
                        val items = lines.mapIndexedNotNull { i, ln ->
                            val q = (parseNum(retQty[i]) ?: 0.0).coerceIn(0.0, ln.quantity)
                            if (q > 0.0) ReturnItemReq(ln.product_id, q, ln.unit_price, q * ln.unit_price) else null
                        }
                        if (items.isEmpty()) { err = tr("Choose a quantity to return"); return@Button }
                        saving = true; err = null
                        scope.launch {
                            try {
                                val r = ApiClient.get().createReturn(CreateReturnRequest(s.id, reason, refund, items))
                                if (r.status == "success") onDone() else { err = r.message ?: tr("Couldn't process"); saving = false }
                            } catch (e: Exception) { err = tr("Couldn't reach the server"); saving = false }
                        }
                    },
                    enabled = !saving, modifier = Modifier.fillMaxWidth().height(52.dp),
                ) {
                    if (saving) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp,
                        color = MaterialTheme.colorScheme.onPrimary)
                    else Text(tr("Process refund"), style = MaterialTheme.typography.labelLarge)
                }
            }
        }
    }
}

// ══════════════════════════════════════════════════════════════════════════════
//  CUSTOMERS — list + add + per-customer credit settings
// ══════════════════════════════════════════════════════════════════════════════
private fun creditModeLabel(m: String?) = when (m) {
    "unlimited" -> tr("Unlimited credit"); "limited" -> tr("Limited credit"); else -> tr("No credit")
}

@Composable
fun CustomersScreen(snackbar: SnackbarHostState) {
    var rows by remember { mutableStateOf<List<Customer>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var showAdd by remember { mutableStateOf(false) }
    var editC by remember { mutableStateOf<Customer?>(null) }
    val scope = rememberCoroutineScope()
    suspend fun load() { rows = try { ApiClient.get().customers().data } catch (e: Exception) { emptyList() } }
    LaunchedEffect(Unit) { load(); loading = false }

    Box(Modifier.fillMaxSize()) {
        when {
            loading -> SkeletonList()
            rows.isEmpty() -> EmptyState(Icons.Default.People, tr("No customers yet"),
                tr("Add customers to track loyalty and credit."), ctaText = tr("Add customer"), onCta = { showAdd = true })
            else -> LazyColumn(
                contentPadding = PaddingValues(16.dp, 16.dp, 16.dp, 90.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) { items(rows, key = { it.id }) { c -> CustomerRow(c) { editC = c } } }
        }
        if (rows.isNotEmpty()) ExtendedFloatingActionButton(
            onClick = { showAdd = true },
            icon = { Icon(Icons.Default.Add, null) }, text = { Text(tr("Add customer")) },
            modifier = Modifier.align(Alignment.BottomEnd).padding(20.dp),
        )
    }
    if (showAdd) AddCustomerSheet(
        onDismiss = { showAdd = false },
        onCreated = { showAdd = false; scope.launch { loading = true; load(); loading = false; snackbar.showSnackbar(tr("Customer added")) } })
    if (editC != null) CustomerCreditSheet(
        customer = editC!!, onDismiss = { editC = null },
        onSaved = { editC = null; scope.launch { loading = true; load(); loading = false; snackbar.showSnackbar(tr("Credit settings saved")) } })
}

@Composable
private fun CustomerRow(c: Customer, onClick: () -> Unit) {
    GlowCard(Modifier.fillMaxWidth(), onClick = onClick) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(c.name ?: "—", fontWeight = FontWeight.Bold)
                Text(listOfNotNull(c.phone?.ifBlank { null }, creditModeLabel(c.credit_mode)).joinToString(" · "),
                    style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            if (c.credit_balance > 0.005) Text(tr("owes") + " " + money(c.credit_balance),
                fontWeight = FontWeight.Bold, color = Warning)
        }
    }
}

@Composable
private fun AddCustomerSheet(onDismiss: () -> Unit, onCreated: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    var name by remember { mutableStateOf("") }
    var phone by remember { mutableStateOf("") }
    var email by remember { mutableStateOf("") }
    var saving by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf<String?>(null) }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(Modifier.padding(20.dp).padding(bottom = 24.dp)) {
            Text(tr("Add Customer"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(16.dp))
            OutlinedTextField(name, { name = it }, label = { Text(tr("Full name *")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(phone, { phone = it }, label = { Text(tr("Phone")) }, singleLine = true, modifier = Modifier.weight(1f))
                OutlinedTextField(email, { email = it }, label = { Text(tr("Email")) }, singleLine = true, modifier = Modifier.weight(1f))
            }
            if (err != null) { Spacer(Modifier.height(10.dp)); Text(err!!, color = MaterialTheme.colorScheme.error) }
            Spacer(Modifier.height(20.dp))
            Button(onClick = {
                if (name.isBlank()) { err = tr("Name is required"); return@Button }
                saving = true; err = null
                scope.launch {
                    try {
                        val r = ApiClient.get().createCustomer(CreateCustomerRequest(name.trim(), phone.trim(), email.trim()))
                        if (r.status == "success") onCreated() else { err = r.message ?: tr("Couldn't save"); saving = false }
                    } catch (e: Exception) { err = tr("Couldn't reach the server"); saving = false }
                }
            }, enabled = !saving, modifier = Modifier.fillMaxWidth().height(52.dp)) {
                if (saving) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary)
                else Text(tr("Save Customer"), style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}

@Composable
private fun CustomerCreditSheet(customer: Customer, onDismiss: () -> Unit, onSaved: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    var mode by remember { mutableStateOf(customer.credit_mode ?: "none") }
    var modeMenu by remember { mutableStateOf(false) }
    var limit by remember { mutableStateOf(if (customer.credit_limit > 0) customer.credit_limit.toString() else "") }
    var saving by remember { mutableStateOf(false) }
    val modes = listOf("none" to tr("No credit"), "limited" to tr("Limited"), "unlimited" to tr("Unlimited"))
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(Modifier.padding(20.dp).padding(bottom = 24.dp)) {
            Text(customer.name ?: tr("Customer"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Text(tr("Outstanding") + " " + money(customer.credit_balance),
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(16.dp))
            Text(tr("Credit mode"), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Box {
                OutlinedButton(onClick = { modeMenu = true }, modifier = Modifier.fillMaxWidth()) {
                    Text(modes.firstOrNull { it.first == mode }?.second ?: tr("No credit"), modifier = Modifier.weight(1f))
                    Icon(Icons.Default.ArrowDropDown, null)
                }
                DropdownMenu(expanded = modeMenu, onDismissRequest = { modeMenu = false }) {
                    modes.forEach { (k, lbl) -> DropdownMenuItem(text = { Text(lbl) }, onClick = { mode = k; modeMenu = false }) }
                }
            }
            if (mode == "limited") {
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(limit, { limit = it }, label = { Text(tr("Credit limit")) }, singleLine = true,
                    keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                        keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal),
                    modifier = Modifier.fillMaxWidth())
            }
            Spacer(Modifier.height(20.dp))
            Button(onClick = {
                saving = true
                scope.launch {
                    try {
                        val r = ApiClient.get().updateCustomerCredit(customer.id,
                            UpdateCustomerCreditRequest(mode, if (mode == "limited") (parseNum(limit) ?: 0.0) else 0.0))
                        if (r.status == "success") onSaved() else saving = false
                    } catch (e: Exception) { saving = false }
                }
            }, enabled = !saving, modifier = Modifier.fillMaxWidth().height(52.dp)) {
                if (saving) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary)
                else Text(tr("Save credit settings"), style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}

// ══════════════════════════════════════════════════════════════════════════════
//  RECEIVABLES — who owes you + statement + record payment
// ══════════════════════════════════════════════════════════════════════════════
@Composable
fun ReceivablesScreen(snackbar: SnackbarHostState) {
    var rows by remember { mutableStateOf<List<Customer>>(emptyList()) }
    var total by remember { mutableStateOf(0.0) }
    var loading by remember { mutableStateOf(true) }
    var stmtId by remember { mutableStateOf<Int?>(null) }
    val scope = rememberCoroutineScope()
    suspend fun load() {
        val r = try { ApiClient.get().receivables() } catch (e: Exception) { null }
        rows = r?.data ?: emptyList(); total = r?.total_receivable ?: 0.0
    }
    LaunchedEffect(Unit) { load(); loading = false }

    when {
        loading -> SkeletonList()
        rows.isEmpty() -> EmptyState(Icons.Default.AccountBalanceWallet, tr("Nothing outstanding"),
            tr("Credit sales that aren't fully paid will appear here."))
        else -> LazyColumn(
            contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                GlowCard(Modifier.fillMaxWidth(), glow = Warning) {
                    Column(Modifier.padding(18.dp)) {
                        Text(tr("TOTAL RECEIVABLE"), style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.height(6.dp))
                        Text(money(total), style = MaterialTheme.typography.headlineMedium,
                            fontWeight = FontWeight.ExtraBold, color = Warning)
                    }
                }
            }
            items(rows, key = { it.id }) { c ->
                GlowCard(Modifier.fillMaxWidth(), onClick = { stmtId = c.id }) {
                    Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(c.name ?: "—", fontWeight = FontWeight.Bold)
                            Text(c.phone ?: creditModeLabel(c.credit_mode), style = MaterialTheme.typography.labelMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        Text(money(c.credit_balance), fontWeight = FontWeight.ExtraBold, color = Warning)
                    }
                }
            }
        }
    }
    if (stmtId != null) StatementSheet(
        customerId = stmtId!!, onDismiss = { stmtId = null },
        onPaid = { stmtId = null; scope.launch { loading = true; load(); loading = false; snackbar.showSnackbar(tr("Payment recorded")) } })
}

@Composable
private fun StatementSheet(customerId: Int, onDismiss: () -> Unit, onPaid: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    var stmt by remember { mutableStateOf<CustomerStatement?>(null) }
    var loading by remember { mutableStateOf(true) }
    var payAmount by remember { mutableStateOf("") }
    var paying by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf<String?>(null) }
    suspend fun reload() { stmt = try { ApiClient.get().customerStatement(customerId).data } catch (e: Exception) { null } }
    LaunchedEffect(customerId) { reload(); loading = false }

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(Modifier.padding(20.dp).padding(bottom = 24.dp).verticalScroll(rememberScrollState())) {
            val c = stmt?.customer
            Text(c?.name ?: tr("Statement"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Text(tr("Balance") + " " + money(stmt?.balance ?: 0.0), style = MaterialTheme.typography.titleMedium,
                color = Warning, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(14.dp))
            when {
                loading -> CircularProgressIndicator(Modifier.size(26.dp), strokeWidth = 2.dp)
                stmt == null -> Text(tr("Couldn't load statement."), color = MaterialTheme.colorScheme.error)
                else -> {
                    stmt!!.events.forEach { e ->
                        Row(Modifier.fillMaxWidth().padding(vertical = 5.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(if (e.kind == "charge") tr("Credit sale") + " ${e.ref ?: ""}" else tr("Payment") + " ${e.ref ?: ""}",
                                    maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text(shortDate(e.date), style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            Text((if (e.kind == "charge") "+" else "-") + money(e.amount),
                                color = if (e.kind == "charge") Warning else Success, fontWeight = FontWeight.Medium)
                            Spacer(Modifier.width(8.dp))
                            Text(money(e.running_balance), fontWeight = FontWeight.Bold,
                                modifier = Modifier.widthIn(min = 56.dp), textAlign = androidx.compose.ui.text.style.TextAlign.End)
                            if (e.kind == "payment" && e.payment_id != null) {
                                TextButton(onClick = {
                                    scope.launch {
                                        try { if (ApiClient.get().voidPayment(e.payment_id).status == "success") onPaid() } catch (ex: Exception) {}
                                    }
                                }, contentPadding = PaddingValues(horizontal = 6.dp)) {
                                    Text(tr("Void"), style = MaterialTheme.typography.labelSmall)
                                }
                            }
                        }
                    }
                    HorizontalDivider(Modifier.padding(vertical = 12.dp))
                    Text(tr("Record a payment"), fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(8.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                        OutlinedTextField(payAmount, { payAmount = it }, label = { Text(tr("Amount")) }, singleLine = true,
                            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                                keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal),
                            modifier = Modifier.weight(1f))
                        Button(onClick = {
                            val amt = parseNum(payAmount)
                            if (amt == null || amt <= 0) { err = tr("Enter a valid amount"); return@Button }
                            paying = true; err = null
                            scope.launch {
                                try {
                                    val r = ApiClient.get().customerPayment(customerId, PaymentRequest(amt, "cash"))
                                    if (r.status == "success") onPaid() else { err = r.message ?: tr("Failed"); paying = false }
                                } catch (e: Exception) { err = tr("Couldn't reach the server"); paying = false }
                            }
                        }, enabled = !paying, modifier = Modifier.height(52.dp)) {
                            if (paying) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary)
                            else Text(tr("Pay"))
                        }
                    }
                    if (err != null) { Spacer(Modifier.height(8.dp)); Text(err!!, color = MaterialTheme.colorScheme.error) }
                }
            }
        }
    }
}

// ══════════════════════════════════════════════════════════════════════════════
//  SUPPLIERS — list + add
// ══════════════════════════════════════════════════════════════════════════════
@Composable
fun SuppliersScreen(snackbar: SnackbarHostState) {
    var suppliers by remember { mutableStateOf<List<Supplier>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var showAdd by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    suspend fun load() { suppliers = try { ApiClient.get().suppliers().data } catch (e: Exception) { emptyList() } }
    LaunchedEffect(Unit) { load(); loading = false }

    Box(Modifier.fillMaxSize()) {
        when {
            loading -> SkeletonList()
            suppliers.isEmpty() -> EmptyState(Icons.Default.LocalShipping, tr("No suppliers yet"),
                tr("Add the vendors you buy stock from."), ctaText = tr("Add Supplier"), onCta = { showAdd = true })
            else -> LazyColumn(
                contentPadding = PaddingValues(16.dp, 16.dp, 16.dp, 90.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                items(suppliers, key = { it.id }) { sup -> SupplierRow(sup) }
            }
        }
        if (suppliers.isNotEmpty()) ExtendedFloatingActionButton(
            onClick = { showAdd = true },
            icon = { Icon(Icons.Default.Add, null) }, text = { Text(tr("Add Supplier")) },
            modifier = Modifier.align(Alignment.BottomEnd).padding(20.dp),
        )
    }

    if (showAdd) AddSupplierSheet(
        onDismiss = { showAdd = false },
        onCreated = { showAdd = false; scope.launch { loading = true; load(); loading = false; snackbar.showSnackbar(tr("Supplier added")) } },
    )
}

@Composable
private fun SupplierRow(s: Supplier) {
    GlowCard(Modifier.fillMaxWidth()) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(s.name ?: "—", fontWeight = FontWeight.Bold)
                val contact = listOfNotNull(s.phone?.ifBlank { null }, s.email?.ifBlank { null }).joinToString(" · ")
                if (contact.isNotBlank()) Text(contact, style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            AssistChip(onClick = {}, label = { Text("${s.order_count} " + tr("PO")) })
        }
    }
}

@Composable
private fun AddSupplierSheet(onDismiss: () -> Unit, onCreated: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    var name by remember { mutableStateOf("") }
    var phone by remember { mutableStateOf("") }
    var email by remember { mutableStateOf("") }
    var address by remember { mutableStateOf("") }
    var saving by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(Modifier.padding(20.dp).padding(bottom = 24.dp).verticalScroll(rememberScrollState())) {
            Text(tr("Add Supplier"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(16.dp))
            OutlinedTextField(name, { name = it }, label = { Text(tr("Company name *")) },
                singleLine = true, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(phone, { phone = it }, label = { Text(tr("Phone")) },
                    singleLine = true, modifier = Modifier.weight(1f))
                OutlinedTextField(email, { email = it }, label = { Text(tr("Email")) },
                    singleLine = true, modifier = Modifier.weight(1f))
            }
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(address, { address = it }, label = { Text(tr("Address")) },
                singleLine = true, modifier = Modifier.fillMaxWidth())
            if (error != null) { Spacer(Modifier.height(10.dp)); Text(error!!, color = MaterialTheme.colorScheme.error) }
            Spacer(Modifier.height(20.dp))
            Button(
                onClick = {
                    if (name.isBlank()) { error = tr("Company name is required"); return@Button }
                    saving = true; error = null
                    scope.launch {
                        try {
                            val r = ApiClient.get().createSupplier(CreateSupplierRequest(
                                name = name.trim(), phone = phone.trim(), email = email.trim(), address = address.trim()))
                            if (r.status == "success") onCreated() else error = r.message ?: tr("Couldn't save")
                        } catch (e: Exception) { error = tr("Couldn't reach the server") } finally { saving = false }
                    }
                },
                enabled = !saving, modifier = Modifier.fillMaxWidth().height(52.dp),
            ) {
                if (saving) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.onPrimary)
                else Text(tr("Save Supplier"), style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}

// ══════════════════════════════════════════════════════════════════════════════
//  PAYABLES — what you owe suppliers + statement + record payment
// ══════════════════════════════════════════════════════════════════════════════
@Composable
fun PayablesScreen(snackbar: SnackbarHostState) {
    var rows by remember { mutableStateOf<List<Supplier>>(emptyList()) }
    var total by remember { mutableStateOf(0.0) }
    var loading by remember { mutableStateOf(true) }
    var stmtId by remember { mutableStateOf<Int?>(null) }
    val scope = rememberCoroutineScope()
    suspend fun load() {
        val r = try { ApiClient.get().payables() } catch (e: Exception) { null }
        rows = r?.data ?: emptyList(); total = r?.total_payable ?: 0.0
    }
    LaunchedEffect(Unit) { load(); loading = false }

    when {
        loading -> SkeletonList()
        rows.isEmpty() -> EmptyState(Icons.Default.Payments, tr("Nothing owed"),
            tr("Unpaid purchase orders will appear here."))
        else -> LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            item {
                GlowCard(Modifier.fillMaxWidth(), glow = MaterialTheme.colorScheme.error) {
                    Column(Modifier.padding(18.dp)) {
                        Text(tr("TOTAL PAYABLE"), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.height(6.dp))
                        Text(money(total), style = MaterialTheme.typography.headlineMedium,
                            fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.error)
                    }
                }
            }
            items(rows, key = { it.id }) { s ->
                GlowCard(Modifier.fillMaxWidth(), onClick = { stmtId = s.id }) {
                    Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(s.name ?: "—", fontWeight = FontWeight.Bold)
                            Text(s.phone ?: (s.payment_terms ?: "none"), style = MaterialTheme.typography.labelMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        Text(money(s.credit_balance), fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.error)
                    }
                }
            }
        }
    }
    if (stmtId != null) SupplierStatementSheet(
        supplierId = stmtId!!, onDismiss = { stmtId = null },
        onPaid = { stmtId = null; scope.launch { loading = true; load(); loading = false; snackbar.showSnackbar(tr("Payment recorded")) } })
}

@Composable
private fun SupplierStatementSheet(supplierId: Int, onDismiss: () -> Unit, onPaid: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    var stmt by remember { mutableStateOf<SupplierStatement?>(null) }
    var loading by remember { mutableStateOf(true) }
    var payAmount by remember { mutableStateOf("") }
    var paying by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(supplierId) {
        stmt = try { ApiClient.get().supplierStatement(supplierId).data } catch (e: Exception) { null }
        loading = false
    }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(Modifier.padding(20.dp).padding(bottom = 24.dp).verticalScroll(rememberScrollState())) {
            Text(stmt?.supplier?.name ?: tr("Statement"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Text(tr("You owe") + " " + money(stmt?.balance ?: 0.0), style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.error, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(14.dp))
            when {
                loading -> CircularProgressIndicator(Modifier.size(26.dp), strokeWidth = 2.dp)
                stmt == null -> Text(tr("Couldn't load statement."), color = MaterialTheme.colorScheme.error)
                else -> {
                    stmt!!.events.forEach { e ->
                        Row(Modifier.fillMaxWidth().padding(vertical = 5.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(if (e.kind == "charge") tr("PO") + " ${e.ref ?: ""}" else tr("Payment") + " ${e.ref ?: ""}",
                                    maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text(shortDate(e.date), style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            Text((if (e.kind == "charge") "+" else "-") + money(e.amount),
                                color = if (e.kind == "charge") MaterialTheme.colorScheme.error else Success, fontWeight = FontWeight.Medium)
                            Spacer(Modifier.width(8.dp))
                            Text(money(e.running_balance), fontWeight = FontWeight.Bold,
                                modifier = Modifier.widthIn(min = 56.dp), textAlign = androidx.compose.ui.text.style.TextAlign.End)
                            if (e.kind == "payment" && e.payment_id != null) {
                                TextButton(onClick = {
                                    scope.launch {
                                        try { if (ApiClient.get().voidPayment(e.payment_id).status == "success") onPaid() } catch (ex: Exception) {}
                                    }
                                }, contentPadding = PaddingValues(horizontal = 6.dp)) {
                                    Text(tr("Void"), style = MaterialTheme.typography.labelSmall)
                                }
                            }
                        }
                    }
                    HorizontalDivider(Modifier.padding(vertical = 12.dp))
                    Text(tr("Record a payment"), fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(8.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                        OutlinedTextField(payAmount, { payAmount = it }, label = { Text(tr("Amount")) }, singleLine = true,
                            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                                keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal),
                            modifier = Modifier.weight(1f))
                        Button(onClick = {
                            val amt = parseNum(payAmount)
                            if (amt == null || amt <= 0) { err = tr("Enter a valid amount"); return@Button }
                            paying = true; err = null
                            scope.launch {
                                try {
                                    val r = ApiClient.get().supplierPayment(supplierId, PaymentRequest(amt, "cash"))
                                    if (r.status == "success") onPaid() else { err = r.message ?: tr("Failed"); paying = false }
                                } catch (e: Exception) { err = tr("Couldn't reach the server"); paying = false }
                            }
                        }, enabled = !paying, modifier = Modifier.height(52.dp)) {
                            if (paying) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary)
                            else Text(tr("Pay"))
                        }
                    }
                    if (err != null) { Spacer(Modifier.height(8.dp)); Text(err!!, color = MaterialTheme.colorScheme.error) }
                }
            }
        }
    }
}

// ══════════════════════════════════════════════════════════════════════════════
//  DAILY CASH SUMMARY
// ══════════════════════════════════════════════════════════════════════════════
@Composable
fun DailyCashScreen(snackbar: SnackbarHostState) {
    var date by remember { mutableStateOf(java.time.LocalDate.now()) }
    var data by remember { mutableStateOf<DailyCash?>(null) }
    var loading by remember { mutableStateOf(true) }
    LaunchedEffect(date) {
        loading = true
        data = try { ApiClient.get().dailyCash(date.toString()).data } catch (e: Exception) { null }
        loading = false
    }
    val d = data ?: DailyCash()
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = { date = date.minusDays(1) }) { Text(tr("‹ Prev")) }
            Text(date.toString(), Modifier.weight(1f), textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            TextButton(onClick = { if (date.isBefore(java.time.LocalDate.now())) date = date.plusDays(1) }) { Text(tr("Next ›")) }
        }
        if (loading) {
            Box(Modifier.fillMaxWidth().padding(30.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
        } else {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                StatCard(tr("Cash In"), money(d.cash_in), tr("received"), Success, Modifier.weight(1f))
                StatCard(tr("Cash Out"), money(d.cash_out), tr("paid out"), MaterialTheme.colorScheme.error, Modifier.weight(1f))
            }
            StatCard(tr("Net Cash"), money(d.net), tr("in − out"), MaterialTheme.colorScheme.primary, Modifier.fillMaxWidth())
            if (d.by_method.isNotEmpty()) {
                SectionHeader(tr("By method"))
                GlowCard(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        d.by_method.forEach { m ->
                            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                Text((m.method ?: "—").replaceFirstChar { it.uppercase() }, Modifier.weight(1f))
                                Text(if (m.direction == "in") tr("IN") else tr("OUT"), style = MaterialTheme.typography.labelSmall,
                                    color = if (m.direction == "in") Success else MaterialTheme.colorScheme.error)
                                Spacer(Modifier.width(12.dp))
                                Text(money(m.amount), fontWeight = FontWeight.Bold)
                            }
                        }
                    }
                }
            }
        }
    }
}

// ══════════════════════════════════════════════════════════════════════════════
//  AGING — receivables & payables by age bucket
// ══════════════════════════════════════════════════════════════════════════════
@Composable
private fun agingColor(i: Int): Color = when (i) {
    0 -> Success; 1 -> Info; 2 -> Warning; else -> MaterialTheme.colorScheme.error
}

@Composable
fun AgingScreen(snackbar: SnackbarHostState) {
    var ar by remember { mutableStateOf<AgingBuckets?>(null) }
    var ap by remember { mutableStateOf<AgingBuckets?>(null) }
    var loading by remember { mutableStateOf(true) }
    var showType by remember { mutableStateOf("receivable") }
    LaunchedEffect(Unit) {
        ar = try { ApiClient.get().aging("receivable").data } catch (e: Exception) { null }
        ap = try { ApiClient.get().aging("payable").data } catch (e: Exception) { null }
        loading = false
    }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)) {
        LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            items(listOf("receivable" to tr("Receivable"), "payable" to tr("Payable"))) { (k, lbl) ->
                FilterChip(selected = showType == k, onClick = { showType = k }, label = { Text(lbl) })
            }
        }
        if (loading) {
            Box(Modifier.fillMaxWidth().padding(30.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
        } else {
            val isAr = showType == "receivable"
            val b = if (isAr) ar else ap
            val rows = listOf(
                tr("Current") to (b?.current ?: 0.0), tr("1–30 days") to (b?.d1_30 ?: 0.0),
                tr("31–60 days") to (b?.d31_60 ?: 0.0), tr("61–90 days") to (b?.d61_90 ?: 0.0),
                tr("90+ days") to (b?.d90_plus ?: 0.0))
            val total = rows.sumOf { it.second }
            val accent = if (isAr) Warning else MaterialTheme.colorScheme.error
            GlowCard(Modifier.fillMaxWidth(), glow = accent) {
                Column(Modifier.padding(18.dp)) {
                    Text(if (isAr) tr("TOTAL RECEIVABLE") else tr("TOTAL PAYABLE"),
                        style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.height(6.dp))
                    Text(money(total), style = MaterialTheme.typography.headlineMedium,
                        fontWeight = FontWeight.ExtraBold, color = accent)
                }
            }
            GlowCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    rows.forEachIndexed { i, (label, amt) ->
                        Row(Modifier.fillMaxWidth().padding(vertical = 9.dp), verticalAlignment = Alignment.CenterVertically) {
                            Text(label, Modifier.weight(1f))
                            Text(money(amt), fontWeight = FontWeight.Bold, color = agingColor(i))
                        }
                        if (i < rows.lastIndex) HorizontalDivider()
                    }
                }
            }
            Text(tr("Each bucket holds the outstanding balance of parties whose oldest unpaid document falls in that age range."),
                style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

// ══════════════════════════════════════════════════════════════════════════════
//  RETAIL SETTINGS — credit enforcement, defaults, currency, payment methods
// ══════════════════════════════════════════════════════════════════════════════
@Composable
fun RetailSettingsScreen(snackbar: SnackbarHostState) {
    var s by remember { mutableStateOf(CreditSettings()) }
    var methods by remember { mutableStateOf<List<PayMethod>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var saving by remember { mutableStateOf(false) }
    var newMethod by remember { mutableStateOf("") }
    var enforceMenu by remember { mutableStateOf(false) }
    var modeMenu by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    suspend fun loadMethods() { methods = try { ApiClient.get().payMethods().data } catch (e: Exception) { emptyList() } }
    LaunchedEffect(Unit) {
        s = try { ApiClient.get().creditSettingsGet().data ?: CreditSettings() } catch (e: Exception) { CreditSettings() }
        loadMethods(); loading = false
    }

    if (loading) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
        return
    }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)) {
        SectionHeader(tr("Credit policy"))
        Text(tr("Default credit mode for new customers"), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        val modes = listOf("none" to tr("No credit"), "limited" to tr("Limited"), "unlimited" to tr("Unlimited"))
        Box {
            OutlinedButton(onClick = { modeMenu = true }, modifier = Modifier.fillMaxWidth()) {
                Text(modes.firstOrNull { it.first == s.default_credit_mode }?.second ?: tr("No credit"), modifier = Modifier.weight(1f))
                Icon(Icons.Default.ArrowDropDown, null)
            }
            DropdownMenu(expanded = modeMenu, onDismissRequest = { modeMenu = false }) {
                modes.forEach { (k, lbl) -> DropdownMenuItem(text = { Text(lbl) }, onClick = { s = s.copy(default_credit_mode = k); modeMenu = false }) }
            }
        }
        OutlinedTextField(s.default_credit_limit, { s = s.copy(default_credit_limit = it) },
            label = { Text(tr("Default credit limit")) }, singleLine = true,
            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal),
            modifier = Modifier.fillMaxWidth())

        Text(tr("When a credit limit is exceeded"), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        val enforce = listOf("warn" to tr("Warn (allow the sale)"), "block" to tr("Block the sale"))
        Box {
            OutlinedButton(onClick = { enforceMenu = true }, modifier = Modifier.fillMaxWidth()) {
                Text(enforce.firstOrNull { it.first == s.enforce_credit_limit }?.second ?: tr("Warn"), modifier = Modifier.weight(1f))
                Icon(Icons.Default.ArrowDropDown, null)
            }
            DropdownMenu(expanded = enforceMenu, onDismissRequest = { enforceMenu = false }) {
                enforce.forEach { (k, lbl) -> DropdownMenuItem(text = { Text(lbl) }, onClick = { s = s.copy(enforce_credit_limit = k); enforceMenu = false }) }
            }
        }
        OutlinedTextField(s.base_currency, { s = s.copy(base_currency = it) },
            label = { Text(tr("Base currency (e.g. USD)")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
        Button(onClick = {
            saving = true
            scope.launch {
                try { ApiClient.get().creditSettingsSet(s); snackbar.showSnackbar(tr("Settings saved")) }
                catch (e: Exception) { snackbar.showSnackbar(tr("Couldn't save")) } finally { saving = false }
            }
        }, enabled = !saving, modifier = Modifier.fillMaxWidth().height(50.dp)) {
            if (saving) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary)
            else Text(tr("Save settings"))
        }

        SectionHeader(tr("Payment methods"))
        methods.forEach { m ->
            GlowCard(Modifier.fillMaxWidth()) {
                Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(m.name ?: "—", Modifier.weight(1f), fontWeight = FontWeight.Medium)
                    Text(m.type ?: "", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(newMethod, { newMethod = it }, label = { Text(tr("New method")) }, singleLine = true, modifier = Modifier.weight(1f))
            Button(onClick = {
                val n = newMethod.trim()
                if (n.isNotBlank()) scope.launch {
                    try { ApiClient.get().addPayMethod(CreatePayMethodRequest(n)); newMethod = ""; loadMethods() }
                    catch (e: Exception) { snackbar.showSnackbar(tr("Couldn't add")) }
                }
            }, modifier = Modifier.height(52.dp)) { Text(tr("Add")) }
        }
    }
}

// ══════════════════════════════════════════════════════════════════════════════
//  PURCHASE ORDERS — list + create + view + receive
// ══════════════════════════════════════════════════════════════════════════════
@Composable
fun PurchaseOrdersScreen(snackbar: SnackbarHostState) {
    var orders by remember { mutableStateOf<List<PurchaseOrder>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var showCreate by remember { mutableStateOf(false) }
    var detailId by remember { mutableStateOf<Int?>(null) }
    val scope = rememberCoroutineScope()

    suspend fun load() { orders = try { ApiClient.get().purchaseOrders().data } catch (e: Exception) { emptyList() } }
    LaunchedEffect(Unit) { load(); loading = false }

    Box(Modifier.fillMaxSize()) {
        when {
            loading -> SkeletonList()
            orders.isEmpty() -> EmptyState(Icons.Default.Inventory2, tr("No purchase orders"),
                tr("Create a PO to restock from a supplier."), ctaText = tr("New Purchase Order"), onCta = { showCreate = true })
            else -> LazyColumn(
                contentPadding = PaddingValues(16.dp, 16.dp, 16.dp, 90.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                items(orders, key = { it.id }) { po -> PoRow(po) { detailId = po.id } }
            }
        }
        if (orders.isNotEmpty()) ExtendedFloatingActionButton(
            onClick = { showCreate = true },
            icon = { Icon(Icons.Default.Add, null) }, text = { Text(tr("New PO")) },
            modifier = Modifier.align(Alignment.BottomEnd).padding(20.dp),
        )
    }

    if (showCreate) CreatePoSheet(
        onDismiss = { showCreate = false },
        onCreated = { showCreate = false; scope.launch { loading = true; load(); loading = false; snackbar.showSnackbar(tr("Purchase order created")) } },
    )
    if (detailId != null) PoDetailSheet(
        poId = detailId!!,
        onDismiss = { detailId = null },
        onReceived = { detailId = null; scope.launch { loading = true; load(); loading = false; snackbar.showSnackbar(tr("Stock received")) } },
    )
}

@Composable
private fun statusColor(status: String?): Color = when (status) {
    "received" -> Success
    "pending" -> Warning
    "cancelled" -> MaterialTheme.colorScheme.error
    else -> Info
}

@Composable
private fun payStatusColor(status: String?): Color = when (status) {
    "paid" -> Success
    "partial" -> Warning
    else -> MaterialTheme.colorScheme.error
}

@Composable
private fun PoRow(po: PurchaseOrder, onClick: () -> Unit) {
    GlowCard(Modifier.fillMaxWidth(), onClick = onClick) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(po.po_number ?: "—", fontWeight = FontWeight.Bold)
                Text(po.supplier_name ?: "—", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            Column(horizontalAlignment = Alignment.End) {
                Text(money(po.total), fontWeight = FontWeight.ExtraBold)
                Text((po.status ?: "").replaceFirstChar { it.uppercase() },
                    style = MaterialTheme.typography.labelMedium, color = statusColor(po.status),
                    fontWeight = FontWeight.Bold)
                Text((po.payment_status ?: "unpaid").replaceFirstChar { it.uppercase() },
                    style = MaterialTheme.typography.labelSmall, color = payStatusColor(po.payment_status))
            }
        }
    }
}

@Composable
private fun PoDetailSheet(poId: Int, onDismiss: () -> Unit, onReceived: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    var detail by remember { mutableStateOf<PoDetail?>(null) }
    var loading by remember { mutableStateOf(true) }
    var receiving by remember { mutableStateOf(false) }
    var payAmt by remember { mutableStateOf("") }
    var paying by remember { mutableStateOf(false) }
    LaunchedEffect(poId) {
        detail = try { ApiClient.get().purchaseOrder(poId).data } catch (e: Exception) { null }
        loading = false
    }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(Modifier.padding(20.dp).padding(bottom = 24.dp).verticalScroll(rememberScrollState())) {
            val po = detail?.po
            Text(po?.po_number ?: tr("Purchase order"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Text("${po?.supplier_name ?: "—"} · ${(po?.status ?: "").replaceFirstChar { it.uppercase() }}",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(16.dp))
            when {
                loading -> CircularProgressIndicator(Modifier.size(28.dp), strokeWidth = 2.dp)
                detail == null -> Text(tr("Couldn't load this purchase order."), color = MaterialTheme.colorScheme.error)
                else -> {
                    detail!!.items.forEach { it ->
                        Row(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
                            Column(Modifier.weight(1f)) {
                                Text(it.product_name ?: tr("Item"), maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text("${fmtQty(it.quantity)} × ${money(it.unit_cost)}",
                                    style = MaterialTheme.typography.labelMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            Text(money(it.total), fontWeight = FontWeight.Medium)
                        }
                    }
                    HorizontalDivider(Modifier.padding(vertical = 10.dp))
                    SummaryRow(tr("Total"), money(po?.total ?: 0.0), bold = true)
                    SummaryRow(tr("Paid"), money(po?.amount_paid ?: 0.0))
                    SummaryRow(tr("Balance"), money((po?.total ?: 0.0) - (po?.amount_paid ?: 0.0)))
                    Text(tr("Payment:") + " " + (po?.payment_status ?: "unpaid").replaceFirstChar { it.uppercase() },
                        style = MaterialTheme.typography.labelMedium, color = payStatusColor(po?.payment_status))
                    if ((po?.payment_status ?: "unpaid") != "paid") {
                        Spacer(Modifier.height(12.dp))
                        Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                            OutlinedTextField(payAmt, { payAmt = it }, label = { Text(tr("Pay supplier")) }, singleLine = true,
                                keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                                    keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal),
                                modifier = Modifier.weight(1f))
                            Button(onClick = {
                                val amt = parseNum(payAmt)
                                if (amt != null && amt > 0) {
                                    paying = true
                                    scope.launch {
                                        try {
                                            val r = ApiClient.get().payPurchaseOrder(poId, PaymentRequest(amt, "cash"))
                                            if (r.status == "success") onReceived() else paying = false
                                        } catch (e: Exception) { paying = false }
                                    }
                                }
                            }, enabled = !paying, modifier = Modifier.height(52.dp)) {
                                if (paying) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary)
                                else Text(tr("Pay"))
                            }
                        }
                    }
                    if (po?.status == "pending") {
                        Spacer(Modifier.height(16.dp))
                        Button(
                            onClick = {
                                receiving = true
                                scope.launch {
                                    try {
                                        val r = ApiClient.get().receivePurchaseOrder(poId)
                                        if (r.status == "success") onReceived() else receiving = false
                                    } catch (e: Exception) { receiving = false }
                                }
                            },
                            enabled = !receiving, modifier = Modifier.fillMaxWidth().height(52.dp),
                        ) {
                            if (receiving) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp,
                                color = MaterialTheme.colorScheme.onPrimary)
                            else Text(tr("Receive stock"), style = MaterialTheme.typography.labelLarge)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun CreatePoSheet(onDismiss: () -> Unit, onCreated: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    var suppliers by remember { mutableStateOf<List<Supplier>>(emptyList()) }
    var products by remember { mutableStateOf<List<Product>>(emptyList()) }
    LaunchedEffect(Unit) {
        suppliers = try { ApiClient.get().suppliers().data } catch (e: Exception) { emptyList() }
        products = try { ApiClient.get().products().data } catch (e: Exception) { emptyList() }
    }

    var supplier by remember { mutableStateOf<Supplier?>(null) }
    var supplierMenu by remember { mutableStateOf(false) }
    var product by remember { mutableStateOf<Product?>(null) }
    var productMenu by remember { mutableStateOf(false) }
    var qty by remember { mutableStateOf("1") }
    var cost by remember { mutableStateOf("") }
    val lines = remember { mutableStateListOf<Triple<Product, Double, Double>>() } // product, qty, unitCost
    var saving by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var amountPaid by remember { mutableStateOf("") }

    val numeric = androidx.compose.foundation.text.KeyboardOptions(
        keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal)
    val poTotal = lines.sumOf { it.second * it.third }

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(Modifier.padding(20.dp).padding(bottom = 24.dp).verticalScroll(rememberScrollState())) {
            Text(tr("New Purchase Order"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(16.dp))

            // Supplier picker
            Box {
                OutlinedButton(onClick = { supplierMenu = true }, modifier = Modifier.fillMaxWidth()) {
                    Text(supplier?.name ?: tr("Select supplier"), modifier = Modifier.weight(1f))
                    Icon(Icons.Default.ChevronRight, null)
                }
                DropdownMenu(expanded = supplierMenu, onDismissRequest = { supplierMenu = false }) {
                    if (suppliers.isEmpty()) DropdownMenuItem(text = { Text(tr("No suppliers — add one first")) }, onClick = { supplierMenu = false })
                    suppliers.forEach { s ->
                        DropdownMenuItem(text = { Text(s.name ?: "—") }, onClick = { supplier = s; supplierMenu = false })
                    }
                }
            }

            Spacer(Modifier.height(16.dp))
            Text(tr("Items"), fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))

            // Product picker + qty + cost + add
            Box {
                OutlinedButton(onClick = { productMenu = true }, modifier = Modifier.fillMaxWidth()) {
                    Text(product?.let { "${it.name} (${it.sku})" } ?: tr("Select product"), modifier = Modifier.weight(1f),
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Icon(Icons.Default.ChevronRight, null)
                }
                DropdownMenu(expanded = productMenu, onDismissRequest = { productMenu = false }) {
                    products.forEach { p ->
                        DropdownMenuItem(text = { Text("${p.name} (${p.sku})") },
                            onClick = { product = p; if (cost.isBlank()) cost = ""; productMenu = false })
                    }
                }
            }
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(qty, { qty = it }, label = { Text(tr("Qty")) }, singleLine = true,
                    keyboardOptions = numeric, modifier = Modifier.weight(1f))
                OutlinedTextField(cost, { cost = it }, label = { Text(tr("Unit cost")) }, singleLine = true,
                    keyboardOptions = numeric, modifier = Modifier.weight(1f))
                FilledTonalIconButton(onClick = {
                    val p = product; val q = parseNum(qty); val c = parseNum(cost)
                    if (p != null && q != null && q > 0 && c != null && c >= 0) {
                        lines.add(Triple(p, q, c)); product = null; qty = "1"; cost = ""
                    }
                }) { Icon(Icons.Default.Add, tr("Add item")) }
            }

            if (lines.isNotEmpty()) {
                Spacer(Modifier.height(12.dp))
                lines.forEachIndexed { i, (p, q, c) ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text("${p.name}  ×${fmtQty(q)}", Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(money(q * c), fontWeight = FontWeight.Medium)
                        TextButton(onClick = { lines.removeAt(i) }) { Text(tr("Remove")) }
                    }
                }
                HorizontalDivider(Modifier.padding(vertical = 8.dp))
                SummaryRow(tr("Order total"), money(poTotal), bold = true)
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(amountPaid, { amountPaid = it },
                    label = { Text(tr("Amount paid now (blank = on credit)")) }, singleLine = true,
                    keyboardOptions = numeric, modifier = Modifier.fillMaxWidth())
            }

            if (error != null) { Spacer(Modifier.height(10.dp)); Text(error!!, color = MaterialTheme.colorScheme.error) }
            Spacer(Modifier.height(20.dp))
            Button(
                onClick = {
                    val sup = supplier
                    if (sup == null) { error = tr("Select a supplier"); return@Button }
                    if (lines.isEmpty()) { error = tr("Add at least one item"); return@Button }
                    saving = true; error = null
                    scope.launch {
                        try {
                            val r = ApiClient.get().createPurchaseOrder(CreatePoRequest(
                                supplier_id = sup.id,
                                items = lines.map { PoItemReq(it.first.id, it.second, it.third) },
                                amount_paid = parseNum(amountPaid) ?: 0.0))
                            if (r.status == "success") onCreated() else error = r.message ?: tr("Couldn't save")
                        } catch (e: Exception) { error = tr("Couldn't reach the server") } finally { saving = false }
                    }
                },
                enabled = !saving, modifier = Modifier.fillMaxWidth().height(52.dp),
            ) {
                if (saving) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.onPrimary)
                else Text(tr("Create Purchase Order"), style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}
