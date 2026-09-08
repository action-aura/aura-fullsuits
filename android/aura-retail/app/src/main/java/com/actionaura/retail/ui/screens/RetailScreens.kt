@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class, androidx.compose.foundation.layout.ExperimentalLayoutApi::class)

package com.actionaura.retail.ui.screens

import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Inventory2
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Share
import androidx.compose.material.icons.filled.ShoppingCart
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.*
import com.actionaura.retail.ui.CAP_STOCK_ADJUST
import com.actionaura.retail.ui.RetailSession
import com.actionaura.retail.ui.components.EmptyState
import com.actionaura.retail.ui.components.TillCard
import com.actionaura.retail.ui.theme.CategoryPalette
import com.actionaura.retail.ui.theme.Danger
import com.actionaura.retail.ui.theme.OnAccent
import com.actionaura.retail.ui.theme.Success
import com.actionaura.retail.ui.theme.SuccessContainer
import com.actionaura.retail.ui.theme.Warning
import com.actionaura.retail.ui.i18n.amount
import com.actionaura.retail.ui.i18n.fmtQty
import com.actionaura.retail.ui.i18n.money
import com.actionaura.retail.ui.i18n.parseIntFlexible
import com.actionaura.retail.ui.i18n.parseNum
import com.actionaura.retail.ui.i18n.tr
import kotlinx.coroutines.launch
import java.util.UUID

// ── Category color tiles (offline "product image" treatment) ──────────────────
// DECORATIVE identity palette, not semantic -- these hues exist only so two
// categories look different, and are exempt from the token layer for that
// reason (ColorTokenContractTest.kt). They are however drawn TWICE:
// as a quiet 18%-alpha tile backdrop AND as the full-strength colour of the
// text/icon sitting on that same tile (see catColor's call sites below), so
// the pairing still owes a WCAG check same as any other text-on-surface pair.
// Measured against every surface this tile can land on (SurfaceRaised,
// SurfaceTill, SurfacePanel, SurfaceApp) at both the 18%-tinted-backdrop
// reading and the raw (category-name label, no tint) reading, four of the
// original eight hues came in under the 4.5:1 floor:
//   indigo 6366F1  worst 3.11 (tinted) / 3.62 (raw)
//   pink   EC4899  worst 3.90 (tinted) / 4.59 (raw)   -- passed raw, failed tinted
//   purple A855F7  worst 3.33 (tinted) / 4.09 (raw)
//   red    EF4444  worst 3.60 (tinted) / 4.30 (raw)
// Per the audit instructions, the fix is to lighten those ONE-BY-ONE minimally
// (same hue, same saturation, HSL lightness nudged up just far enough to clear
// 4.5:1 at the worst-case surface) rather than replace the palette -- the
// point of these hues is that they differ, and after the nudge they still do.
// teal/amber/emerald/sky were already compliant (4.74-7.55 worst-case) and are
// untouched. avatarPalette in ui/components/Components.kt is the same eight
// hues (different order) and got the identical nudge for the identical reason.
// The values themselves moved to ui/theme/Color.kt (CategoryPalette) on
// 2026-09-06 so that every colour in the app is painted from one file; the
// measurements above are repeated there next to the numbers.
private val catPalette = CategoryPalette
private fun catColor(key: String?): Color {
    val k = key ?: ""
    return catPalette[(k.hashCode().let { if (it < 0) -it else it }) % catPalette.size]
}

// A parked POS cart (Hold / Resume). Held in a process-wide singleton so parked
// sales survive navigating away from POS while the app is running.
data class HeldSale(val id: Long, val items: Map<String, Double>, val total: Double, val count: Int)
object HeldSales { val list = androidx.compose.runtime.mutableStateListOf<HeldSale>() }

@Composable
fun PosScreen(snackbar: SnackbarHostState) {
    var products by remember { mutableStateOf<List<Product>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var query by remember { mutableStateOf("") }
    var category by remember { mutableStateOf("All") }
    val cart = remember { mutableStateMapOf<String, Double>() } // productId -> qty
    var showCart by remember { mutableStateOf(false) }
    var charging by remember { mutableStateOf(false) }
    // Wave 1B (Part O): holds the full authoritative SaleResult (not just its
    // total) so the success screen can offer a real receipt -- every field
    // shown there is this server response, never a locally-recomputed value.
    var successSale by remember { mutableStateOf<com.actionaura.retail.net.SaleResult?>(null) }
    var showScanner by remember { mutableStateOf(false) }
    var lastScanned by remember { mutableStateOf<String?>(null) }
    var paymentMethod by remember { mutableStateOf("cash") }
    var showHeld by remember { mutableStateOf(false) }
    var customers by remember { mutableStateOf<List<Customer>>(emptyList()) }
    var customer by remember { mutableStateOf<Customer?>(null) }   // null = walk-in
    var customerMenu by remember { mutableStateOf(false) }
    var downPayment by remember { mutableStateOf("") }             // optional paid-now on a credit sale
    var payMethods by remember { mutableStateOf<List<PayMethod>>(emptyList()) }  // configurable tenders
    val scope = rememberCoroutineScope()

    suspend fun load() { products = try { ApiClient.get().products().data } catch (e: Exception) { emptyList() } }
    suspend fun loadCustomers() { customers = try { ApiClient.get().customers().data } catch (e: Exception) { emptyList() } }
    suspend fun loadMethods() { payMethods = try { ApiClient.get().payMethods().data } catch (e: Exception) { emptyList() } }
    LaunchedEffect(Unit) { loading = true; load(); loadCustomers(); loadMethods(); loading = false }

    val byId = products.associateBy { it.id }
    val total = cart.entries.sumOf { (id, qty) -> (byId[id]?.sell_price ?: 0.0) * qty }
    val count = cart.values.sumOf { it }.toInt()

    // Stock guard: never let the cart exceed what's actually on hand. Returns false
    // (and adds nothing) when the next unit would oversell.
    fun addOne(p: Product): Boolean {
        val current = cart[p.id] ?: 0.0
        if (current + 1 > p.total_stock) return false
        cart[p.id] = current + 1
        return true
    }

    // Wave 1B (Part L/M): a completed USB/Bluetooth HID scan (MainActivity's
    // dispatchKeyEvent -> HidScanBus) is routed through the exact same
    // lookup+add-to-cart logic as a CameraX/ML Kit camera scan below --
    // neither the cart nor the cashier can tell which physical source a
    // given scan came from, by design.
    //
    // Bug fix: HidScanBus.lastScan is a process-wide singleton with no "only
    // while POS is on screen" gate, so this LaunchedEffect used to fire
    // immediately with whatever scan was already sitting there the moment
    // PosScreen (re-)entered composition -- e.g. the cashier scanned an item
    // while on another tab, or simply reopened POS after already scanning
    // earlier in the session (the cart above is plain `remember` and gets
    // discarded on tab switch, but HidScanBus.lastScan is not). That stale
    // scan would get silently applied to the now-different cart. We remember
    // the seq already seen at the moment this screen entered composition and
    // only ever act on a strictly newer one (see isUnconsumedScan).
    var lastConsumedScanSeq by remember {
        mutableStateOf(com.actionaura.retail.barcode.HidScanBus.lastScan?.seq ?: -1L)
    }
    LaunchedEffect(com.actionaura.retail.barcode.HidScanBus.lastScan) {
        val event = com.actionaura.retail.barcode.HidScanBus.lastScan ?: return@LaunchedEffect
        if (!com.actionaura.retail.barcode.isUnconsumedScan(event, lastConsumedScanSeq)) return@LaunchedEffect
        lastConsumedScanSeq = event.seq
        // Launch-readiness "the POS scale fix": resolves against the server
        // (one indexed row) instead of linear-scanning the fully fetched
        // `products` list -- see barcode/ProductLookup.kt's
        // lookupProductByCode. Already inside this LaunchedEffect's suspend
        // scope, so the suspend call is awaited directly, off the main
        // thread the same way load()/loadCustomers()/loadMethods() above are.
        when (val result = com.actionaura.retail.barcode.lookupProductByCode(ApiClient.get(), event.code)) {
            is com.actionaura.retail.barcode.ProductLookupResult.Found -> {
                val p = result.product
                lastScanned = if (addOne(p)) "✓ ${p.name}"
                              else "✗ ${p.name}: " + tr("Only %s in stock").format(fmtQty(p.total_stock))
            }
            is com.actionaura.retail.barcode.ProductLookupResult.NotFound ->
                lastScanned = "✗ " + tr("Not found:") + " ${event.code}"
            // Distinct from NotFound on purpose: the lookup itself failed
            // (offline, a non-404 server error), so telling the cashier this
            // item doesn't exist would blame the product for a network
            // problem. apiErrorMessage gives an honest, specific reason
            // (including the licensing-block case) instead of a bare
            // "not found" -- same mapping every other screen's catch block uses.
            is com.actionaura.retail.barcode.ProductLookupResult.Failed ->
                lastScanned = "✗ " + apiErrorMessage(result.cause)
        }
    }

    // Park the current cart so another sale can be rung up, then resumed later.
    fun holdCurrent() {
        if (cart.isEmpty()) return
        val t = cart.entries.sumOf { (id, q) -> (byId[id]?.sell_price ?: 0.0) * q }
        val c = cart.values.sumOf { it }.toInt()
        HeldSales.list.add(HeldSale(System.currentTimeMillis(), cart.toMap(), t, c))
        cart.clear()
    }
    fun resumeHeld(h: HeldSale) {
        if (cart.isNotEmpty()) holdCurrent()   // park whatever is in the cart first
        cart.clear(); cart.putAll(h.items)
        HeldSales.list.remove(h)
        showHeld = false
    }

    val categories = remember(products) {
        listOf("All") + products.mapNotNull { it.category_name?.takeIf { c -> c.isNotBlank() } }.distinct()
    }
    val filtered = products.filter {
        (category == "All" || it.category_name == category) &&
            (query.isBlank() || (it.name ?: "").contains(query, true) || (it.sku ?: "").contains(query, true))
    }

    Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize()) {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedTextField(
                    value = query, onValueChange = { query = it },
                    placeholder = { Text(tr("Search products")) },
                    leadingIcon = { Icon(Icons.Default.Search, null) },
                    singleLine = true, shape = RoundedCornerShape(28.dp),
                    modifier = Modifier.weight(1f),
                )
                // Optional camera scan — manual search above always remains available.
                FilledTonalIconButton(
                    onClick = { showScanner = true }, modifier = Modifier.size(52.dp),
                ) { Icon(Icons.Default.QrCodeScanner, contentDescription = tr("Scan barcode")) }
                if (HeldSales.list.isNotEmpty()) {
                    FilledTonalButton(onClick = { showHeld = true }) { Text(tr("Held") + " ${HeldSales.list.size}") }
                }
            }
            if (categories.size > 1) {
                LazyRow(
                    contentPadding = PaddingValues(horizontal = 16.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    items(categories) { c ->
                        FilterChip(selected = category == c, onClick = { category = c }, label = { Text(c) })
                    }
                }
                Spacer(Modifier.height(8.dp))
            }

            when {
                loading -> SkeletonGrid()
                filtered.isEmpty() -> EmptyState(
                    icon = Icons.Default.Inventory2,
                    title = if (products.isEmpty()) tr("No products yet") else tr("No matches"),
                    subtitle = if (products.isEmpty()) tr("Add products to start selling.")
                               else tr("Try another search or category."),
                )
                else -> LazyVerticalGrid(
                    columns = GridCells.Adaptive(160.dp),
                    contentPadding = PaddingValues(12.dp, 0.dp, 12.dp, 110.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                    modifier = Modifier.weight(1f),
                ) {
                    items(filtered, key = { it.id }) { p ->
                        ProductTile(p, inCart = (cart[p.id] ?: 0.0).toInt(),
                            onAdd = { if (!addOne(p)) scope.launch { snackbar.showSnackbar(tr("Only %s in stock").format(fmtQty(p.total_stock))) } })
                    }
                }
            }
        }

        // Animated floating cart bar
        AnimatedVisibility(
            visible = count > 0,
            enter = slideInVertically(tween(280)) { it } + fadeIn(),
            exit = slideOutVertically(tween(220)) { it } + fadeOut(),
            modifier = Modifier.align(Alignment.BottomCenter),
        ) {
            // Quiet neutral elevation instead of the old infinite pulseGlow
            // animation -- a floating bar earns its lift with a shadow, not by
            // breathing forever (and the pulse kept this screen recomposing
            // whenever the cart had an item; see Components.kt's header note).
            Surface(
                color = MaterialTheme.colorScheme.primary, shadowElevation = 6.dp,
                modifier = Modifier.fillMaxWidth().padding(12.dp),
                shape = MaterialTheme.shapes.large,
                onClick = { showCart = true },
            ) {
                Row(Modifier.padding(horizontal = 18.dp, vertical = 16.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    Box(contentAlignment = Alignment.Center) {
                        Icon(Icons.Default.ShoppingCart, null, tint = MaterialTheme.colorScheme.onPrimary)
                        Surface(color = MaterialTheme.colorScheme.onPrimary, shape = CircleShape,
                            modifier = Modifier.align(Alignment.TopEnd).offset(x = 8.dp, y = (-8).dp)) {
                            Text("$count", color = MaterialTheme.colorScheme.primary,
                                style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Bold,
                                modifier = Modifier.padding(horizontal = 6.dp, vertical = 1.dp))
                        }
                    }
                    Spacer(Modifier.width(14.dp))
                    Text(tr("View cart"), color = MaterialTheme.colorScheme.onPrimary,
                        fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                    Text(money(total), color = MaterialTheme.colorScheme.onPrimary,
                        style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                }
            }
        }

        // Payment success overlay
        AnimatedVisibility(successSale != null, enter = fadeIn(), exit = fadeOut()) {
            PaymentSuccess(sale = successSale ?: com.actionaura.retail.net.SaleResult(), onNewSale = { successSale = null })
        }
    }

    if (showCart) {
        val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
        ModalBottomSheet(onDismissRequest = { showCart = false }, sheetState = sheet) {
            Column(Modifier.padding(20.dp).padding(bottom = 24.dp)) {
                Text(tr("Current Sale"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(10.dp))
                // Customer (required for credit; walk-in otherwise)
                Box {
                    OutlinedButton(onClick = { customerMenu = true }, modifier = Modifier.fillMaxWidth()) {
                        Icon(Icons.Default.Person, null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp))
                        Text(customer?.name ?: tr("Walk-in customer"), modifier = Modifier.weight(1f))
                        Icon(Icons.Default.ArrowDropDown, null)
                    }
                    DropdownMenu(expanded = customerMenu, onDismissRequest = { customerMenu = false }) {
                        DropdownMenuItem(text = { Text(tr("Walk-in (no customer)")) },
                            onClick = { customer = null; customerMenu = false })
                        customers.forEach { c ->
                            DropdownMenuItem(text = { Text(c.name ?: "—") },
                                onClick = { customer = c; customerMenu = false })
                        }
                    }
                }
                customer?.let { c ->
                    if (c.credit_balance > 0.005 || (c.credit_mode ?: "none") != "none") {
                        Text(
                            tr("Outstanding") + " ${money(c.credit_balance)}" +
                                when (c.credit_mode) {
                                    "limited" -> " · " + tr("limit") + " ${money(c.credit_limit)}"
                                    "unlimited" -> " · " + tr("unlimited credit")
                                    "none" -> " · " + tr("no credit")
                                    else -> ""
                                },
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(top = 4.dp, start = 4.dp),
                        )
                    }
                }
                Spacer(Modifier.height(12.dp))
                LazyColumn(Modifier.heightIn(max = 340.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    items(cart.keys.toList()) { id ->
                        val p = byId[id] ?: return@items
                        val qty = cart[id] ?: 0.0
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Box(Modifier.size(40.dp).clip(RoundedCornerShape(10.dp))
                                .background(catColor(p.category_name).copy(alpha = 0.18f)),
                                contentAlignment = Alignment.Center) {
                                Text((p.name ?: "?").take(1).uppercase(), color = catColor(p.category_name),
                                    fontWeight = FontWeight.Bold)
                            }
                            Spacer(Modifier.width(12.dp))
                            Column(Modifier.weight(1f)) {
                                Text(p.name ?: "—", fontWeight = FontWeight.Medium, maxLines = 1,
                                    overflow = TextOverflow.Ellipsis)
                                Text(money(p.sell_price), style = MaterialTheme.typography.labelMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            FilledTonalIconButton(onClick = {
                                val n = qty - 1; if (n <= 0) cart.remove(id) else cart[id] = n
                            }, modifier = Modifier.size(34.dp)) { Icon(Icons.Default.Remove, "−") }
                            Text(fmtQty(qty), fontWeight = FontWeight.Bold,
                                modifier = Modifier.widthIn(min = 28.dp), textAlign = androidx.compose.ui.text.style.TextAlign.Center)
                            FilledTonalIconButton(onClick = {
                                if (!addOne(p)) scope.launch { snackbar.showSnackbar(tr("Max stock: %s").format(fmtQty(p.total_stock))) }
                            }, modifier = Modifier.size(34.dp)) { Icon(Icons.Default.Add, "+") }
                        }
                    }
                }
                Spacer(Modifier.height(10.dp))
                HorizontalDivider()
                Spacer(Modifier.height(10.dp))
                // SUBTOTAL, not "Total" -- the figure this row shows is the
                // same local `total` the checkout button below calls, in its
                // own words, a "pre-tax, pre-discount PREVIEW only" that is
                // "never persisted/displayed as the sale's actual total".
                // That last claim was false: this row displayed it under the
                // label "Total" while the server charged the taxed figure, so
                // a cashier read one number aloud to the customer and the till
                // took another.
                //
                // Fixed by making the LABEL honest rather than by computing
                // tax here. A second pricing engine on the client is the exact
                // drift this product already paid for once (AUDIT-002, the
                // Android zero-tax defect) and the reason
                // docs/architecture/financial-authority-contracts.md makes the
                // server the only authority. The desktop does compute a live
                // total client-side, but only because retail_pricing_parity_
                // test.py pins its arithmetic to the server's line by line;
                // Kotlin has no such harness, and inventing one to win a label
                // is a far larger and riskier change than telling the truth.
                Row {
                    Text(tr("Subtotal"), style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                    Text(money(total), style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.primary)
                }
                Spacer(Modifier.height(4.dp))
                Text(tr("Tax and discounts are applied at checkout"),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.height(14.dp))
                Text(tr("Payment method"), style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.height(6.dp))
                // Tenders come from the configurable payment-methods list (Settings); "Credit"
                // (on account) is always appended. Falls back to a basic set if none load.
                // The VALUE (it.lowercase()) is the wire code sent to the server and must
                // never change; only the LABEL passed to tr() below is translated. The five
                // names retail_api.py seeds (_DEFAULT_METHODS) have Arabic entries in
                // Strings.kt; a shop's own custom method name has none and tr() falls back
                // to that name itself, so nothing breaks for it.
                val payOptions = (
                    if (payMethods.isNotEmpty()) payMethods.mapNotNull { it.name }.map { it to it.lowercase() }
                    else listOf("Cash" to "cash", "Card" to "card", "Transfer" to "transfer")
                ) + ("Credit" to "credit")
                LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(payOptions) { (label, value) ->
                        FilterChip(selected = paymentMethod == value, onClick = { paymentMethod = value },
                            label = { Text(tr(label)) })
                    }
                }
                if (paymentMethod == "credit") {
                    Spacer(Modifier.height(8.dp))
                    OutlinedTextField(downPayment, { downPayment = it },
                        label = { Text(tr("Paid now (optional) — rest goes on credit")) }, singleLine = true,
                        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                            keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal),
                        modifier = Modifier.fillMaxWidth())
                }
                Spacer(Modifier.height(16.dp))
                OutlinedButton(onClick = { holdCurrent(); showCart = false },
                    modifier = Modifier.fillMaxWidth().height(48.dp)) { Text(tr("Hold sale (park for later)")) }
                Spacer(Modifier.height(8.dp))
                Button(
                    onClick = {
                        val pm = paymentMethod
                        val cust = customer
                        if (pm == "credit" && cust == null) {
                            scope.launch { snackbar.showSnackbar(tr("Select a customer for credit sales (walk-in not allowed)")) }
                        } else {
                            charging = true
                            // `total` here is a local, pre-tax, pre-discount PREVIEW only
                            // (Phase 4D classification A) -- used to clamp the optional
                            // credit down-payment and nothing else. It is never sent as
                            // an authoritative financial field and never persisted/
                            // displayed as the sale's actual total; only the backend's
                            // response (r.data.total below) is.
                            val previewTotal = total
                            // MOB-001 fix: for anything other than an explicit credit
                            // down-payment, amount_paid must be omitted (null), not
                            // previewTotal -- previewTotal is pre-tax/pre-discount, so
                            // sending it as "amount paid" on a taxed sale always
                            // under-reports the tender, which the server correctly
                            // read as a partial payment and rejected as an implicit
                            // credit sale requiring a customer (reproduced on a real
                            // device: every taxed product's "cash" checkout failed this
                            // way). Omitting it lets the server default amount_paid to
                            // its own computed, authoritative total -- guaranteeing a
                            // "pay in full" sale is always recorded as fully paid.
                            val paidNow: Double? = if (pm == "credit") (parseNum(downPayment)?.coerceIn(0.0, previewTotal) ?: 0.0) else null
                            // Commercial intent only: product_id + quantity per line (no
                            // discount-entry UI exists in this source, so discount_pct
                            // stays at its default of 0). unit_price/tax_rate/subtotal/
                            // discount_amount/tax_amount/total are never sent -- the
                            // server always resolves/computes them from the product row
                            // and ignores any client-submitted equivalent (Wave 0,
                            // AUDIT-002/003). See docs/architecture/financial-authority-contracts.md.
                            val items = cart.entries.map { (id, qty) -> SaleItemReq(id, qty) }
                            scope.launch {
                                try {
                                    val r = ApiClient.get().createSale(CreateSaleRequest(
                                        amount_paid = paidNow, payment_method = pm, customer_id = cust?.id,
                                        items = items, idempotency_key = UUID.randomUUID().toString()))
                                    if (r.status == "success") {
                                        // Authoritative result: always the backend's own
                                        // response, never the local preview above -- this is
                                        // what fixes the historical Android zero-tax defect
                                        // (AUDIT-002), since previewTotal never included tax
                                        // or a server-validated discount at all.
                                        cart.clear(); showCart = false; successSale = r.data
                                        paymentMethod = "cash"; customer = null; downPayment = ""
                                        r.data?.warning?.takeIf { it.isNotBlank() }?.let { snackbar.showSnackbar(it) }
                                        load(); loadCustomers()   // refresh stock + customer balances
                                    } else snackbar.showSnackbar(r.message ?: tr("Sale failed"))
                                // apiErrorMessage (net/ApiErrors.kt): a licensing
                                // 403 on checkout must say the subscription
                                // blocked the sale, never "server unreachable".
                                } catch (e: Exception) { snackbar.showSnackbar(apiErrorMessage(e)) }
                                finally { charging = false }
                            }
                        }
                    },
                    enabled = !charging, modifier = Modifier.fillMaxWidth().height(54.dp),
                ) {
                    if (charging) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp,
                        color = MaterialTheme.colorScheme.onPrimary)
                    else Text(tr("Charge") + "  " + money(total), style = MaterialTheme.typography.labelLarge)
                }
            }
        }
    }

    if (showHeld) {
        ModalBottomSheet(onDismissRequest = { showHeld = false }) {
            Column(Modifier.padding(20.dp).padding(bottom = 24.dp)) {
                Text(tr("Held sales"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(4.dp))
                Text(tr("Resuming parks the current cart first, so nothing is lost."),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.height(12.dp))
                if (HeldSales.list.isEmpty())
                    Text(tr("No held sales."), color = MaterialTheme.colorScheme.onSurfaceVariant)
                HeldSales.list.toList().forEach { h ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(tr("%d item(s)").format(h.count), fontWeight = FontWeight.Bold)
                            Text(money(h.total), style = MaterialTheme.typography.labelMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        TextButton(onClick = { HeldSales.list.remove(h) }) { Text(tr("Discard")) }
                        Spacer(Modifier.width(4.dp))
                        Button(onClick = { resumeHeld(h) }) { Text(tr("Resume")) }
                    }
                }
            }
        }
    }

    if (showScanner) {
        // Continuous scanning: scan item after item; the running total updates live on the
        // scanner overlay; "Done" closes the camera and opens the cart to charge/invoice.
        val scanStatus = (if (lastScanned != null) "$lastScanned   " else "") +
            tr("%d item(s)").format(count) + " · " + money(total)
        BarcodeScannerDialog(
            continuous = true,
            statusText = scanStatus,
            onResult = { code ->
                // onResult is a plain (String) -> Unit callback (CameraX's ML Kit
                // analyzer, dispatched via ContextCompat.getMainExecutor -- see
                // BarcodeScanner.kt), not a suspend lambda, so the lookup goes
                // through the screen's existing rememberCoroutineScope() the same
                // way the stock-limit snackbar above already does.
                scope.launch {
                    when (val result = com.actionaura.retail.barcode.lookupProductByCode(ApiClient.get(), code)) {
                        is com.actionaura.retail.barcode.ProductLookupResult.Found -> {
                            val p = result.product
                            lastScanned = if (addOne(p)) "✓ ${p.name}"
                                          else "✗ ${p.name}: " + tr("Only %s in stock").format(fmtQty(p.total_stock))
                        }
                        is com.actionaura.retail.barcode.ProductLookupResult.NotFound ->
                            lastScanned = "✗ " + tr("Not found:") + " $code"
                        is com.actionaura.retail.barcode.ProductLookupResult.Failed ->
                            lastScanned = "✗ " + apiErrorMessage(result.cause)
                    }
                }
            },
            onDismiss = {
                showScanner = false
                lastScanned = null
                if (count > 0) showCart = true   // proceed to the cart to complete the sale
            },
        )
    }
}

@Composable
private fun ProductTile(p: Product, inCart: Int, onAdd: () -> Unit) {
    val accent = catColor(p.category_name)
    val stock = p.total_stock

    TillCard(accent = accent, shape = RoundedCornerShape(16.dp), onClick = onAdd) {
        Box(
            Modifier.fillMaxWidth().height(84.dp)
                .background(Brush.linearGradient(listOf(accent.copy(alpha = 0.85f), accent.copy(alpha = 0.5f)))),
            contentAlignment = Alignment.Center,
        ) {
            Text((p.name ?: "?").take(1).uppercase(), color = Color.White,
                style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.ExtraBold)
            if (inCart > 0) {
                Surface(color = MaterialTheme.colorScheme.surface, shape = CircleShape, shadowElevation = 2.dp,
                    modifier = Modifier.align(Alignment.TopEnd).padding(6.dp)) {
                    Text("$inCart", color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.Bold,
                        style = MaterialTheme.typography.labelMedium,
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp))
                }
            }
            StockBadge(stock, Modifier.align(Alignment.BottomStart).padding(6.dp))
        }
        Column(Modifier.padding(12.dp)) {
            if (!p.category_name.isNullOrBlank()) {
                Text(p.category_name!!.uppercase(), style = MaterialTheme.typography.labelSmall,
                    color = accent, fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Spacer(Modifier.height(2.dp))
            }
            Text(p.name ?: "—", style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis, minLines = 2)
            Spacer(Modifier.height(6.dp))
            Text(money(p.sell_price), style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
private fun StockBadge(stock: Double, modifier: Modifier = Modifier) {
    // SEMANTIC state colour, not decorative: out/low/in-stock is exactly what
    // Danger/Warning/Success exist for. This badge sits on TOP of the
    // ProductTile's category-colour gradient (any of catPalette's eight
    // hues), so it must stay a fully OPAQUE solid fill to read reliably
    // regardless of what is under it -- the quiet *Container tokens
    // (SuccessContainer/DangerContainer) are translucent-over-surface by
    // design and would take on whatever gradient is behind them here, so
    // they are the wrong tool for this specific spot even though they are
    // the right one in PaymentSuccess below. There is no WarningContainer
    // token (Color.kt only has Success/DangerContainer), so all three
    // branches use the same idiom for internal consistency: the *Text*
    // token itself as the opaque fill, OnAccent (a dark, near-navy label
    // already used for "text on a light/vivid fill" elsewhere) as the text
    // colour. Measured: white text on the old literal fills was ALREADY
    // broken (2.15:1 amber, 2.54:1 green, 3.76:1 red -- none reached AA);
    // OnAccent on the token fills reaches 8.66-10.49:1.
    val (label, color) = when {
        stock <= 0 -> tr("Out") to Danger
        stock <= 5 -> (tr("Low") + " · ${fmtQty(stock)}") to Warning
        else -> tr("%s in stock").format(fmtQty(stock)) to Success
    }
    Surface(color = color, shape = RoundedCornerShape(20.dp), modifier = modifier) {
        Text(label, color = OnAccent, style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp))
    }
}

@Composable
private fun PaymentSuccess(sale: com.actionaura.retail.net.SaleResult, onNewSale: () -> Unit) {
    val check = remember { Animatable(0f) }
    val ctx = androidx.compose.ui.platform.LocalContext.current
    LaunchedEffect(Unit) {
        check.animateTo(1f, spring(dampingRatio = Spring.DampingRatioMediumBouncy, stiffness = Spring.StiffnessLow))
    }
    Surface(color = MaterialTheme.colorScheme.background, modifier = Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize().padding(32.dp), horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center) {
            // Success badge idiom shared with the desktop: state text colour on
            // its own quiet container, not white-on-bright-green (which failed
            // contrast) and not a pulsing glow.
            Box(Modifier.size(110.dp).scale(check.value).clip(CircleShape)
                .background(SuccessContainer), contentAlignment = Alignment.Center) {
                Icon(Icons.Default.Check, null, tint = Success, modifier = Modifier.size(60.dp))
            }
            Spacer(Modifier.height(24.dp))
            Text(tr("Payment successful"), style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))
            Text(money(sale.total) + " " + tr("collected"), style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(4.dp))
            sale.sale_number?.let {
                Text(it, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Spacer(Modifier.height(36.dp))
            // Wave 1B (Part O): no direct thermal-printer protocol on Android
            // this wave (no hardware to verify against) -- the honest,
            // real fallback is a receipt text shared via the OS share sheet,
            // which can itself target a print service, a printing app, chat,
            // email, etc. Every value is `sale`, the server's own response.
            OutlinedButton(onClick = { shareReceipt(ctx, sale) }, modifier = Modifier.fillMaxWidth().height(54.dp)) {
                Icon(Icons.Default.Share, null); Spacer(Modifier.width(8.dp)); Text(tr("Share Receipt"))
            }
            Spacer(Modifier.height(12.dp))
            Button(onClick = onNewSale, modifier = Modifier.fillMaxWidth().height(54.dp)) {
                Text(tr("New Sale"), style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}

// Wave 1B (Part O/P): plain-text receipt built exclusively from `sale`
// (the authoritative server response) -- see PaymentSuccess's docstring.
// No direct USB/Bluetooth thermal printing is implemented or claimed here;
// see docs/hardware/receipt-printer-compatibility-matrix.md.
private fun shareReceipt(ctx: android.content.Context, sale: com.actionaura.retail.net.SaleResult) {
    val text = buildString {
        appendLine("Aura Retail")
        sale.sale_number?.let { appendLine("Receipt #$it") }
        appendLine("Subtotal: ${money(sale.subtotal)}")
        if (sale.discount_amount > 0) appendLine("Discount: -${money(sale.discount_amount)}")
        if (sale.tax_amount > 0) appendLine("Tax: ${money(sale.tax_amount)}")
        appendLine("Total: ${money(sale.total)}")
        appendLine("Paid: ${money(sale.amount_paid)}")
        if (sale.change > 0) appendLine("Change: ${money(sale.change)}")
    }
    val intent = android.content.Intent(android.content.Intent.ACTION_SEND).apply {
        type = "text/plain"
        putExtra(android.content.Intent.EXTRA_TEXT, text)
    }
    // The chooser TITLE is ordinary UI copy on an otherwise fully translated
    // screen and simply missed tr() -- it stayed English while the button that
    // opens it (tr("Share Receipt"), above) translated. Reuses that same
    // catalogue key deliberately rather than minting a near-duplicate
    // "Share receipt": two entries differing only in case is how a catalogue
    // starts drifting from itself.
    //
    // The receipt BODY's own labels are still English literals. That is not an
    // oversight to fix here: DESIGN.md §9 item 3 owns the bilingual receipt
    // template (thermal 58/80 mm and A4, the mark, the fils, the e-invoicing
    // QR) and translating seven labels ahead of it would ship half of a
    // designed artefact. "Aura Retail" stays English regardless -- it is the
    // product name, brand rather than copy.
    ctx.startActivity(android.content.Intent.createChooser(intent, tr("Share Receipt")))
}

@Composable
private fun SkeletonGrid() {
    LazyVerticalGrid(
        columns = GridCells.Adaptive(160.dp),
        contentPadding = PaddingValues(12.dp, 0.dp, 12.dp, 12.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        modifier = Modifier.fillMaxSize(),
    ) {
        items(8) {
            ElevatedCard {
                Column {
                    com.actionaura.retail.ui.components.SkeletonBox(
                        Modifier.fillMaxWidth().height(84.dp), corner = 0.dp)
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        com.actionaura.retail.ui.components.SkeletonBox(Modifier.fillMaxWidth(0.5f).height(10.dp))
                        com.actionaura.retail.ui.components.SkeletonBox(Modifier.fillMaxWidth(0.9f).height(14.dp))
                        com.actionaura.retail.ui.components.SkeletonBox(Modifier.fillMaxWidth(0.4f).height(16.dp))
                    }
                }
            }
        }
    }
}

@Composable
fun ProductsScreen(snackbar: SnackbarHostState) {
    var products by remember { mutableStateOf<List<Product>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var showAdd by remember { mutableStateOf(false) }
    var editProduct by remember { mutableStateOf<Product?>(null) }
    val scope = rememberCoroutineScope()
    // A cashier's server-side grant set is sell/refund/cash-close only -- it
    // does not include CAP_STOCK_ADJUST, so POST /api/sub/retail/products
    // refuses them. Do not show a control that can only fail with a 403.
    val canAddProduct = RetailSession.hasCapability(CAP_STOCK_ADJUST)

    suspend fun load() { products = try { ApiClient.get().products().data } catch (e: Exception) { emptyList() } }
    LaunchedEffect(Unit) { loading = true; load(); loading = false }

    Box(Modifier.fillMaxSize()) {
        when {
            loading -> com.actionaura.retail.ui.components.SkeletonList(count = 8, modifier = Modifier.fillMaxSize())
            products.isEmpty() -> EmptyState(
                icon = Icons.Default.Inventory2,
                title = tr("No products yet"),
                subtitle = tr("Add your first product to start selling."),
                ctaText = if (canAddProduct) tr("Add Product") else null,
                onCta = if (canAddProduct) ({ showAdd = true }) else null,
            )
            else -> LazyColumn(contentPadding = PaddingValues(16.dp, 16.dp, 16.dp, 96.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)) {
                items(products, key = { it.id }) { p ->
                    ElevatedCard(onClick = { editProduct = p }, modifier = Modifier.fillMaxWidth()) {
                        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
                            Box(Modifier.size(46.dp).clip(RoundedCornerShape(12.dp))
                                .background(catColor(p.category_name).copy(alpha = 0.18f)),
                                contentAlignment = Alignment.Center) {
                                Text((p.name ?: "?").take(1).uppercase(), color = catColor(p.category_name),
                                    fontWeight = FontWeight.Bold)
                            }
                            Spacer(Modifier.width(14.dp))
                            Column(Modifier.weight(1f)) {
                                Text(p.name ?: "—", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                                Text("${p.sku ?: ""}  ·  ${p.category_name ?: "—"}",
                                    style = MaterialTheme.typography.labelMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            Column(horizontalAlignment = Alignment.End) {
                                Text(money(p.sell_price), style = MaterialTheme.typography.titleMedium,
                                    fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.primary)
                                Spacer(Modifier.height(4.dp))
                                StockBadge(p.total_stock)
                            }
                        }
                    }
                }
            }
        }

        if (canAddProduct) {
            ExtendedFloatingActionButton(
                onClick = { showAdd = true },
                icon = { Icon(Icons.Default.Add, null) }, text = { Text(tr("Add Product")) },
                modifier = Modifier.align(Alignment.BottomEnd).padding(20.dp),
            )
        }
    }

    if (showAdd) {
        AddProductSheet(
            onDismiss = { showAdd = false },
            onCreated = {
                showAdd = false
                scope.launch { snackbar.showSnackbar(tr("Product added")); loading = true; load(); loading = false }
            },
        )
    }

    if (editProduct != null) {
        EditProductSheet(
            product = editProduct!!,
            onDismiss = { editProduct = null },
            onSaved = { msg ->
                editProduct = null
                scope.launch { snackbar.showSnackbar(msg); loading = true; load(); loading = false }
            },
        )
    }
}

@Composable
private fun EditProductSheet(product: Product, onDismiss: () -> Unit, onSaved: (String) -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    var name by remember { mutableStateOf(product.name ?: "") }
    var price by remember { mutableStateOf(if (product.sell_price > 0) product.sell_price.toString() else "") }
    var cost by remember { mutableStateOf(if (product.cost_price > 0) product.cost_price.toString() else "") }
    var tax by remember { mutableStateOf(if (product.tax_rate > 0) product.tax_rate.toString() else "") }
    var reorder by remember { mutableStateOf(product.reorder_level.toString()) }
    var barcode by remember { mutableStateOf(product.barcode ?: "") }
    var unit by remember { mutableStateOf(product.unit ?: "pcs") }
    var adjust by remember { mutableStateOf("") }          // +add / -remove stock
    var saving by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val numeric = androidx.compose.foundation.text.KeyboardOptions(
        keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal)
    val signed = androidx.compose.foundation.text.KeyboardOptions(
        keyboardType = androidx.compose.ui.text.input.KeyboardType.Number)

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(Modifier.padding(20.dp).padding(bottom = 24.dp)
            .verticalScroll(rememberScrollState())) {
            Text(tr("Edit Product"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Text("${product.sku ?: ""} · ${fmtQty(product.total_stock)} ${product.unit ?: "pcs"} " + tr("in stock"),
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(16.dp))
            OutlinedTextField(name, { name = it }, label = { Text(tr("Product name *")) },
                singleLine = true, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(price, { price = it }, label = { Text(tr("Sell price")) },
                    singleLine = true, keyboardOptions = numeric, modifier = Modifier.weight(1f))
                OutlinedTextField(cost, { cost = it }, label = { Text(tr("Cost price")) },
                    singleLine = true, keyboardOptions = numeric, modifier = Modifier.weight(1f))
            }
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(tax, { tax = it }, label = { Text(tr("Tax %")) },
                    singleLine = true, keyboardOptions = numeric, modifier = Modifier.weight(1f))
                OutlinedTextField(reorder, { reorder = it }, label = { Text(tr("Reorder level")) },
                    singleLine = true, keyboardOptions = numeric, modifier = Modifier.weight(1f))
            }
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(barcode, { barcode = it }, label = { Text(tr("Barcode")) },
                    singleLine = true, modifier = Modifier.weight(1f))
                UnitPicker(unit, { unit = it }, Modifier.weight(1f))
            }

            Spacer(Modifier.height(20.dp))
            Text(tr("Adjust stock"), fontWeight = FontWeight.Bold)
            Text(tr("Enter a positive number to add stock, negative to remove."),
                style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(adjust, { adjust = it }, label = { Text(tr("e.g. +50 or -3")) },
                singleLine = true, keyboardOptions = signed, modifier = Modifier.fillMaxWidth())

            if (error != null) { Spacer(Modifier.height(10.dp)); Text(error!!, color = MaterialTheme.colorScheme.error) }
            Spacer(Modifier.height(20.dp))
            Button(
                onClick = {
                    if (name.isBlank()) { error = tr("Product name is required"); return@Button }
                    saving = true; error = null
                    scope.launch {
                        try {
                            val r = ApiClient.get().updateProduct(product.id, UpdateProductRequest(
                                name = name.trim(),
                                sell_price = parseNum(price) ?: 0.0,
                                cost_price = parseNum(cost) ?: 0.0,
                                tax_rate = parseNum(tax) ?: 0.0,
                                reorder_level = parseIntFlexible(reorder) ?: 0,
                                barcode = barcode.trim(), unit = unit))
                            val adj = parseNum(adjust)
                            if (r.status == "success" && adj != null && adj != 0.0) {
                                ApiClient.get().adjustStock(product.id, AdjustStockRequest(adj))
                            }
                            if (r.status == "success") onSaved(tr("Product updated"))
                            else { error = r.message ?: tr("Couldn't save"); saving = false }
                        } catch (e: Exception) { error = apiErrorMessage(e); saving = false }
                    }
                },
                enabled = !saving, modifier = Modifier.fillMaxWidth().height(52.dp),
            ) {
                if (saving) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.onPrimary)
                else Text(tr("Save Changes"), style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}

private val productUnits = listOf("pcs", "kg", "g", "L", "ml", "box", "pack", "dozen", "pair", "m")

@Composable
private fun UnitPicker(unit: String, onUnit: (String) -> Unit, modifier: Modifier = Modifier) {
    var open by remember { mutableStateOf(false) }
    Box(modifier) {
        OutlinedButton(onClick = { open = true }, modifier = Modifier.fillMaxWidth().height(56.dp)) {
            Text(tr("Unit:") + " $unit", modifier = Modifier.weight(1f))
            Icon(Icons.Default.ArrowDropDown, null)
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            productUnits.forEach { u ->
                DropdownMenuItem(text = { Text(u) }, onClick = { onUnit(u); open = false })
            }
        }
    }
}

@Composable
private fun AddProductSheet(onDismiss: () -> Unit, onCreated: () -> Unit) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var name by remember { mutableStateOf("") }
    var sku by remember { mutableStateOf("") }
    var price by remember { mutableStateOf("") }
    var cost by remember { mutableStateOf("") }
    var stock by remember { mutableStateOf("") }
    var unit by remember { mutableStateOf("pcs") }
    var saving by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var showScan by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val numeric = androidx.compose.foundation.text.KeyboardOptions(
        keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal)

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheet) {
        Column(Modifier.padding(20.dp).padding(bottom = 24.dp)) {
            Text(tr("Add Product"), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(16.dp))
            OutlinedTextField(name, { name = it }, label = { Text(tr("Product name *")) },
                singleLine = true, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(sku, { sku = it }, label = { Text(tr("SKU / barcode *")) },
                singleLine = true, modifier = Modifier.fillMaxWidth(),
                trailingIcon = {
                    IconButton(onClick = { showScan = true }) {
                        Icon(Icons.Default.QrCodeScanner, contentDescription = tr("Scan barcode"))
                    }
                })
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(price, { price = it }, label = { Text(tr("Sell price")) },
                    singleLine = true, keyboardOptions = numeric, modifier = Modifier.weight(1f))
                OutlinedTextField(cost, { cost = it }, label = { Text(tr("Cost price")) },
                    singleLine = true, keyboardOptions = numeric, modifier = Modifier.weight(1f))
            }
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(stock, { stock = it }, label = { Text(tr("Initial stock")) },
                    singleLine = true, keyboardOptions = numeric, modifier = Modifier.weight(1f))
                UnitPicker(unit, { unit = it }, Modifier.weight(1f))
            }
            if (error != null) { Spacer(Modifier.height(10.dp)); Text(error!!, color = MaterialTheme.colorScheme.error) }
            Spacer(Modifier.height(20.dp))
            Button(onClick = {
                if (name.isBlank() || sku.isBlank()) { error = tr("Name and SKU are required"); return@Button }
                saving = true; error = null
                scope.launch {
                    try {
                        val r = ApiClient.get().createProduct(CreateProductRequest(
                            name = name.trim(), sku = sku.trim(),
                            sell_price = parseNum(price) ?: 0.0,
                            cost_price = parseNum(cost) ?: 0.0,
                            initial_stock = parseNum(stock) ?: 0.0, unit = unit))
                        if (r.status == "success") onCreated() else error = r.message ?: tr("Couldn't save")
                    } catch (e: Exception) { error = apiErrorMessage(e) } finally { saving = false }
                }
            }, enabled = !saving, modifier = Modifier.fillMaxWidth().height(52.dp)) {
                if (saving) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.onPrimary)
                else Text(tr("Save Product"), style = MaterialTheme.typography.labelLarge)
            }

            if (showScan) {
                BarcodeScannerDialog(
                    onResult = { code -> sku = code.trim(); showScan = false },
                    onDismiss = { showScan = false },
                )
            }
        }
    }
}
