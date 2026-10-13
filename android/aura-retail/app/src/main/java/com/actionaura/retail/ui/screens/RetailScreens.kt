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
import androidx.compose.material.icons.filled.Print
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
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.actionaura.retail.net.*
import com.actionaura.retail.printer.ReceiptPrintService
import com.actionaura.retail.printer.PrinterPrefs
import com.actionaura.retail.ui.CAP_DISCOUNT
import com.actionaura.retail.ui.CAP_STOCK_ADJUST
import com.actionaura.retail.ui.LocalWideLayout
import com.actionaura.retail.ui.RetailSession
import com.actionaura.retail.ui.pos.*
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
import com.actionaura.retail.ui.pos.DiscountAccess
import com.actionaura.retail.ui.pos.buildSaleItems
import com.actionaura.retail.ui.pos.clampDiscountPct
import com.actionaura.retail.ui.pos.discountAccess
import com.actionaura.retail.ui.pos.buildHoldRequest
import com.actionaura.retail.ui.pos.EstimateLine
import com.actionaura.retail.ui.pos.TenderPartMinor
import com.actionaura.retail.ui.pos.addTenderPart
import com.actionaura.retail.ui.pos.buildCreateSaleRequest
import com.actionaura.retail.ui.pos.effectiveLinePct
import com.actionaura.retail.ui.pos.estimateDueMinor
import com.actionaura.retail.ui.pos.fromMinor
import com.actionaura.retail.ui.pos.parseAmountMinor
import com.actionaura.retail.ui.pos.removeTenderPart
import com.actionaura.retail.ui.pos.summarizeSplit
import com.actionaura.retail.ui.pos.toMinor
import com.actionaura.retail.ui.pos.overLimit
import com.actionaura.retail.ui.CAPS_HOLD_SALE
import com.actionaura.retail.ui.i18n.Currency
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

@Composable
fun PosScreen(snackbar: SnackbarHostState) {
    var products by remember { mutableStateOf<List<Product>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var query by remember { mutableStateOf("") }
    var category by remember { mutableStateOf("All") }
    // ANDROID-3: the cart is a list of LINES (ui/pos/CartLines.kt), not a product -> qty map: a line
    // carries its modifier choice, its own price and its own percentage.
    var cartLines by remember { mutableStateOf<List<CartLine>>(emptyList()) }
    val wide = LocalWideLayout.current
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
    // -- Hold / resume (ANDROID-2a). The parked carts live on the SHOP SERVER (the desktop's own
    // /held-sales routes), not in this process: the old in-memory list died with the app and
    // was invisible to every other till. `heldRows == null` = not loaded yet / load failed.
    var heldCount by remember { mutableStateOf(0) }
    var heldRows by remember { mutableStateOf<List<HeldSaleRow>?>(null) }
    var showHoldDialog by remember { mutableStateOf(false) }
    var holdBusy by remember { mutableStateOf(false) }
    var pendingResume by remember { mutableStateOf<HeldSaleRow?>(null) }
    var pendingDiscard by remember { mutableStateOf<HeldSaleRow?>(null) }
    var customers by remember { mutableStateOf<List<Customer>>(emptyList()) }
    var customer by remember { mutableStateOf<Customer?>(null) }   // null = walk-in
    var customerMenu by remember { mutableStateOf(false) }
    var downPayment by remember { mutableStateOf("") }             // optional paid-now on a credit sale
    var payMethods by remember { mutableStateOf<List<PayMethod>>(emptyList()) }  // configurable tenders
    // ── Loyalty point redemption (retail-hardware-viewports Android wave;
    // desktop has shipped this since schema v27 -- see subsystem-retail.js's
    // `pos-loyalty-redeem`/`_computeLoyaltyRedeem`, the UX this mirrors).
    // `loyaltyPointValue` is the shop's OWN opt-in setting
    // (creditSettingsGet's `loyalty_point_value`, the same endpoint desktop
    // reads it from -- see that screen's own `_loyaltyRedemptionEnabled()`):
    // '0'/unset means the shop never turned redemption on, and the control
    // below must not show at all in that case. A customer's ledger balance
    // accrues from EVERY sale regardless of this setting (retail_api.py:
    // 1 point per $10, unconditional), so gating on balance alone would
    // show a control that always 400s "not configured for this shop".
    var loyaltyPointValue by remember { mutableStateOf(0.0) }
    // Customer-scoped: reset and refetched by the LaunchedEffect(customer)
    // below on every change of `customer`, including back to Walk-in.
    var loyaltyBalance by remember { mutableStateOf(0) }
    var loyaltyRedeemInput by remember { mutableStateOf("") }
    // -- Discount (ANDROID-2a; ui/pos/DiscountRules.kt is the rulebook). The whole-sale
    // percentage is typed as TEXT so a half-typed "7." survives recomposition; each line may
    // also carry its own percentage, which wins for that line. `cashierLimitPct` is the
    // owner's limit for a cashier who does not hold retail.discount (0 = none).
    var cashierLimitPct by remember { mutableStateOf(0.0) }
    var salePctText by remember { mutableStateOf("") }
    var lineMenuKey by remember { mutableStateOf<String?>(null) }
    // -- Sale-level discount (ANDROID-3, the desktop's UI-3a): an amount off or a wanted total, with a
    // reason (ui/pos/CartDiscount.kt). `cartDisc` is INTENT; the server spreads it and answers the total.
    var cartDisc by remember { mutableStateOf<CartDiscount?>(null) }
    var showDiscountSheet by remember { mutableStateOf(false) }
    // Non-null = the "Ask a manager" sheet is open with these people to choose from.
    var approvers by remember { mutableStateOf<List<Approver>?>(null) }
    // -- Variant and modifier pickers (ANDROID-3).
    var variantParent by remember { mutableStateOf<Product?>(null) }
    var variantList by remember { mutableStateOf<List<Product>?>(null) }
    var modifierFor by remember { mutableStateOf<Pair<Product, List<ModifierGroup>>?>(null) }
    val modifierCache = remember { HashMap<String, List<ModifierGroup>>() }
    // -- Split tender (ANDROID-2a; ui/pos/SplitTender.kt is the rulebook). `splitParts` are exact
    // minor units; the amount box is text. The payment chips choose the method of the NEXT part.
    var splitMode by remember { mutableStateOf(false) }
    var splitParts by remember { mutableStateOf<List<TenderPartMinor>>(emptyList()) }
    var splitAmountText by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()
    // Needed here (not just inside PaymentSuccess below) for auto-print:
    // PrinterPrefs is a per-device Context read, and the fire-and-forget
    // auto-print call below fires from this success branch, before
    // PaymentSuccess itself has even entered composition.
    val ctx = androidx.compose.ui.platform.LocalContext.current

    suspend fun load() { products = try { ApiClient.get().products().data } catch (e: Exception) { emptyList() } }
    suspend fun loadCustomers() { customers = try { ApiClient.get().customers().data } catch (e: Exception) { emptyList() } }
    suspend fun loadMethods() { payMethods = try { ApiClient.get().payMethods().data } catch (e: Exception) { emptyList() } }
    // '0' (the string straight off SQLite, same shape RetailExtraScreens.kt's
    // credit-settings screen already reads) means the shop never opened this
    // setting -- parseNum('0') = 0.0, and a missing/malformed value falls to
    // 0.0 via `?: 0.0` the same way, so an older embedded backend that
    // predates this key degrades to "redemption off" rather than crashing.
    suspend fun loadLoyaltySetting() {
        loyaltyPointValue = try {
            parseNum(ApiClient.get().creditSettingsGet().data?.loyalty_point_value) ?: 0.0
        } catch (e: Exception) { 0.0 }
    }
    suspend fun loadCashierLimit() {
        cashierLimitPct = try { ApiClient.get().cashierDiscountLimit().data.limitPct() } catch (e: Exception) { 0.0 }
    }
    LaunchedEffect(Unit) { loading = true; load(); loadCustomers(); loadMethods(); loadLoyaltySetting(); loadCashierLimit(); loading = false }

    // Loyalty balance is CUSTOMER-scoped (a different customer has a
    // different ledger), so it resets and refetches on every change of
    // `customer` -- INCLUDING back to Walk-in -- mirroring
    // subsystem-retail.js's _onCustomerChange(). Best-effort: a failed
    // fetch leaves the control hidden (balance stays 0) rather than showing
    // one against an unknown/stale figure; this must never block a sale.
    LaunchedEffect(customer) {
        loyaltyRedeemInput = ""
        val cust = customer
        loyaltyBalance = if (cust == null) 0 else try {
            val resp = ApiClient.get().customerLoyalty(cust.id)
            kotlin.math.max(0, kotlin.math.floor(resp.data?.balance ?: 0.0).toInt())
        } catch (e: Exception) { 0 }
    }

    val byId = products.associateBy { it.id }
    // Reads RetailSession.capabilities (Compose state), so the control appears or vanishes the
    // moment the session resolves. Fails OPEN while capabilities are unknown, like every other
    // rendering gate here; the server re-gates the sale itself.
    val access = discountAccess(RetailSession.capabilities, cashierLimitPct)
    val salePct = clampDiscountPct(parseNum(salePctText))
    val dpPlaces = Currency.decimals
    fun listPriceOf(l: CartLine) = byId[l.productId]?.sell_price ?: 0.0
    fun unitPriceOf(l: CartLine) = lineUnitPrice(listPriceOf(l), l)
    val total = cartLines.sumOf { unitPriceOf(it) * it.quantity }
    val count = cartItemCount(cartLines)
    // A discount worked out for one cart is only good for that cart: it goes the moment the lines,
    // quantities or prices change. `activeDisc` is what every later line reads, so a stale one can
    // never ride into a sale even for the instant before the effect below clears it.
    val cartSig = cartSignature(cartLines)
    val activeDisc = cartDisc?.takeIf { it.sig == cartSig }
    LaunchedEffect(cartSig) {
        val d = cartDisc
        if (d != null && d.sig != cartSig) {
            cartDisc = null
            snackbar.showSnackbar(tr("Discount cleared — the cart changed"))
        }
    }

    // ── Choosing a product (grid tap, HID scan, camera scan all come through here) ──────────
    // A parent with variants opens the variant picker; a product with extras opens the modifier
    // sheet; anything else goes straight in. Nothing reaches the cart through a picker it needs
    // not to have gone through (the desktop's rule: no line to undo afterwards).
    fun addPlain(p: Product, optionIds: List<String>, picks: List<ModifierPick>): Boolean {
        val out = addToCart(cartLines, p.id, p.total_stock, 1.0, optionIds, picks)
        if (out.accepted) cartLines = out.lines
        return out.accepted
    }
    fun openVariants(parent: Product) {
        variantParent = parent
        val local = products.filter { it.parent_product_id == parent.id }
        variantList = local.takeIf { it.isNotEmpty() }
        // The parent matched but its children are not in the loaded list (a narrow search):
        // ask the server rather than open an empty picker.
        if (local.isEmpty()) scope.launch {
            variantList = try { ApiClient.get().productVariants(parent.id).data } catch (e: Exception) { emptyList() }
        }
    }
    fun openModifiers(p: Product) {
        modifierCache[p.id]?.let { modifierFor = p to it; return }
        scope.launch {
            val groups = try { ApiClient.get().productModifierGroups(p.id).data }
            catch (e: Exception) { snackbar.showSnackbar(apiErrorMessage(e)); return@launch }
            modifierCache[p.id] = groups
            if (groups.isEmpty()) {
                if (!addPlain(p, emptyList(), emptyList())) snackbar.showSnackbar(tr("Only %s in stock").format(fmtQty(p.total_stock)))
            } else modifierFor = p to groups
        }
    }
    fun choose(p: Product): Chosen {
        if (p.has_variants == 1 || products.any { it.parent_product_id == p.id }) { openVariants(p); return Chosen.PICKER }
        if (p.has_modifiers == 1) { openModifiers(p); return Chosen.PICKER }
        return if (addPlain(p, emptyList(), emptyList())) Chosen.ADDED else Chosen.NO_STOCK
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
                lastScanned = scanStatus(p, choose(p))
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

    // Hold / resume against the server's /held-sales routes (the desktop's own).
    suspend fun refreshHeld() {
        val rows = try { ApiClient.get().heldSales().data } catch (e: Exception) { null }
        heldRows = rows
        if (rows != null) heldCount = rows.size
    }
    fun clearSaleState() {
        cartLines = emptyList(); cartDisc = null; salePctText = ""
        downPayment = ""; loyaltyRedeemInput = ""; customer = null; paymentMethod = "cash"
    }
    LaunchedEffect(Unit) { refreshHeld() }

    // Park the cart. The hold body is built in ui/pos/HoldRequestBuilder.kt (the desktop's
    // `_saveHold` shape); subtotal/total in it are display-only, the server re-prices at checkout.
    suspend fun holdCurrent(label: String) {
        if (cartLines.isEmpty()) return
        val heldDiscount = activeDisc != null
        holdBusy = true
        try {
            val r = ApiClient.get().holdSale(buildHoldRequest(
                lines = cartLines, products = byId, salePct = salePct,
                access = access, customerId = customer?.id, label = label, singleMethod = paymentMethod,
                displayTotal = total, dp = Currency.decimals))
            if (r.status == "success") {
                clearSaleState(); showHoldDialog = false; showCart = false
                // The held snapshot keeps the lines (extras, prices, percentages) but not a sale-level
                // amount / target discount - the desktop's hold has no field for it either.
                snackbar.showSnackbar(tr("Sale held") + " \u2014 " + (r.data?.hold_number ?: "") +
                    (if (heldDiscount) " · " + tr("The sale discount is not kept with a held sale.") else ""))
                refreshHeld()
            } else snackbar.showSnackbar(r.message ?: tr("Could not hold sale"))
        } catch (e: Exception) { snackbar.showSnackbar(apiErrorMessage(e)) }
        finally { holdBusy = false }
    }
    // The server DELETES the held row when it hands the cart back, so the cart is rebuilt from
    // the response at once. A non-empty current cart is replaced only after the cashier agreed.
    suspend fun resumeHeld(h: HeldSaleRow) {
        try {
            val r = ApiClient.get().resumeHeldSale(h.id)
            if (r.status != "success") { snackbar.showSnackbar(r.message ?: tr("Could not resume sale")); return }
            val c = parseResumedCart(r.data)
            clearSaleState()
            cartLines = c.lines
            salePctText = if (c.salePct > 0.0) pctText(c.salePct) else ""
            paymentMethod = c.paymentMethod.takeIf { it != "split" } ?: "cash"
            customer = customers.firstOrNull { it.id == c.customerId }
            showHeld = false
            if (cartLines.isNotEmpty()) showCart = true
            // Before the snackbar: showSnackbar suspends until the message goes away, and the "Held n"
            // pill kept counting the sale that had just been taken off the shelf until then.
            refreshHeld()
            snackbar.showSnackbar(tr("Resumed") + " " + (c.holdNumber ?: ""))
        } catch (e: Exception) { snackbar.showSnackbar(apiErrorMessage(e)) }
    }
    suspend fun discardHeld(h: HeldSaleRow) {
        try {
            val r = ApiClient.get().discardHeldSale(h.id)
            snackbar.showSnackbar(if (r.status == "success") tr("Held sale discarded") else (r.message ?: tr("Server error")))
            refreshHeld()
        } catch (e: Exception) { snackbar.showSnackbar(apiErrorMessage(e)) }
    }

    val categories = remember(products) {
        listOf("All") + products.mapNotNull { it.category_name?.takeIf { c -> c.isNotBlank() } }.distinct()
    }
    // A variant is reached through its parent's picker, not as a tile of its own (the desktop's grid).
    val variantCounts = remember(products) { products.mapNotNull { it.parent_product_id }.groupingBy { it }.eachCount() }
    val filtered = products.filter {
        it.parent_product_id.isNullOrBlank() &&
        (category == "All" || it.category_name == category) &&
            (query.isBlank() || (it.name ?: "").contains(query, true) || (it.sku ?: "").contains(query, true))
    }

    // Loyalty point redemption: hidden entirely (not merely disabled) unless
    // ALL FOUR hold -- a named customer, the shop having opted in
    // (loyaltyPointValue > 0), a real ledger balance, and CAP_DISCOUNT.
    // Matches this codebase's "invisible unless opted in" rule for
    // value-shaped settings (CLAUDE.md) the same way e-invoicing and
    // licensing enforcement both already do.
    val loyaltyEligible = customer != null && loyaltyPointValue > 0.0 &&
        loyaltyBalance > 0 && RetailSession.hasCapability(CAP_DISCOUNT)
    // Pure calc: how many points the CURRENT loyaltyRedeemInput value
    // actually redeems against `total` (the same local pre-tax/pre-discount
    // PREVIEW the Subtotal row and the credit down-payment clamp already
    // use), clamped the same three ways create_sale (retail_api.py) clamps
    // server-side: never more than requested, never more than this sale is
    // worth in points, never more than the ledger balance. Reads 0 whenever
    // the control cannot legitimately be showing at all, so a value left
    // over from a previous customer can never silently ride into a sale --
    // mirrors subsystem-retail.js's _loyaltyRedemptionPoints/_loyaltyRedeemPoints.
    val loyaltyMaxAffordable = if (loyaltyPointValue > 0.0) kotlin.math.floor(total / loyaltyPointValue).toInt() else 0
    val loyaltyMaxRedeemable = kotlin.math.min(loyaltyBalance, loyaltyMaxAffordable).coerceAtLeast(0)
    val loyaltyRequestedPoints = if (loyaltyEligible) (parseIntFlexible(loyaltyRedeemInput) ?: 0).coerceAtLeast(0) else 0
    val loyaltyPointsToSend = kotlin.math.min(loyaltyRequestedPoints, loyaltyMaxRedeemable)
    val loyaltyValue = if (loyaltyPointsToSend > 0) kotlin.math.min(loyaltyPointsToSend * loyaltyPointValue, total) else 0.0

    // DISPLAY-ONLY estimate of the amount due (see SaleEstimate.kt / CartDiscount.kt for its limits): the
    // split screen's Remaining, the discount sheet's preview and the "Estimated total" row. The server
    // prices the sale and settles the parts against ITS figure; nothing sent is derived from this.
    val estLines = cartLines.map { l ->
        EstimateLine(unitPriceOf(l), l.quantity,
            if (access is DiscountAccess.None || activeDisc != null) 0.0 else effectiveLinePct(l.ownPct, salePct),
            byId[l.productId]?.tax_rate ?: 0.0)
    }
    val baseTotalMinor = estimateDueMinor(estLines, dpPlaces)
    val grossMinor = grossSubtotalMinor(estLines, dpPlaces)
    val estimateMinor = estimateWithDiscountMinor(estLines, activeDisc, dpPlaces)
    val dueMinor = (estimateMinor - toMinor(loyaltyValue, dpPlaces)).coerceAtLeast(0L)
    // Will the shop ask for a manager's PIN? A prediction from the estimate; a server refusal that says
    // "ask a manager" opens the same sheet (apiFailure.needsManager).
    val needsManager = managerApprovalNeeded(
        holdsDiscountCapability = RetailSession.hasCapability(CAP_DISCOUNT), cashierLimitPct = cashierLimitPct,
        discount = activeDisc,
        effectivePct = activeDisc?.let { effectiveDiscountPct(it, grossMinor, baseTotalMinor) } ?: 0.0,
        hasPriceOverride = cartLines.any { it.unitPriceOverride != null })
    val splitSummary = summarizeSplit(dueMinor, splitParts, customer != null)

    // ── Charge ───────────────────────────────────────────────────────────────────────────
    // Asking a manager: the people the shop offers (GET discount/approvers - a reference and a name,
    // nothing else), then the PIN sheet. Reached when the estimate says a PIN will be needed, or when
    // the server refuses a sale with "ask a manager".
    suspend fun askManager() {
        val list = try { ApiClient.get().discountApprovers().data }
        catch (e: Exception) { snackbar.showSnackbar(apiErrorMessage(e)); return }
        if (list.isEmpty()) snackbar.showSnackbar(tr("No manager has a PIN set up. Ask the owner to set one in Settings > Team."))
        else approvers = list
    }
    // `total` below is a local, pre-tax, pre-discount PREVIEW only (Phase 4D classification A) - used
    // to clamp the optional credit down-payment and nothing else. It is never sent as an authoritative
    // financial field and never shown as the sale's total; only the backend's response is.
    suspend fun submitSale(approval: ApprovalReq?) {
        val pm = paymentMethod
        val cust = customer
        charging = true
        try {
            // MOB-001: for anything other than an explicit credit down-payment, amount_paid is omitted so
            // the server defaults it to its own computed total (a pre-tax figure here read as an
            // under-payment on every taxed product).
            val paidNow: Double? = if (pm == "credit" && !splitMode) (parseNum(downPayment)?.coerceIn(0.0, total) ?: 0.0) else null
            // product_id + quantity + discount_pct per line, plus the extras' option ids and a line's own
            // price (ANDROID-3) - exactly the desktop's shape; unit_price / tax / totals are never sent.
            val items = buildSaleItems(cartLines, salePct, access, cartDiscountOn = activeDisc != null)
            val r = ApiClient.get().createSale(buildCreateSaleRequest(
                items = items, customerId = cust?.id, idempotencyKey = UUID.randomUUID().toString(),
                pointsRedeemed = loyaltyPointsToSend, singleMethod = pm, creditDownPayment = paidNow,
                splitParts = if (splitMode) splitParts else emptyList(), dp = dpPlaces,
                cartDiscount = activeDisc, discountReason = saleDiscountReason(activeDisc, cartLines), approval = approval))
            if (r.status == "success") {
                // Authoritative result: the backend's own response, never the local preview.
                val estimated = fromMinor(estimateMinor, dpPlaces)
                val hadDiscount = activeDisc != null && loyaltyPointsToSend == 0
                cartLines = emptyList(); cartDisc = null; showCart = false; successSale = r.data
                paymentMethod = "cash"; customer = null; downPayment = ""; loyaltyRedeemInput = ""
                salePctText = ""
                splitMode = false; splitParts = emptyList(); splitAmountText = ""
                r.data?.warning?.takeIf { it.isNotBlank() }?.let { snackbar.showSnackbar(it) }
                // The estimate was the phone's guess; say plainly when the shop charged something else.
                r.data?.let { sale ->
                    if (hadDiscount && kotlin.math.abs(sale.total - estimated) > halfMinorUnit(dpPlaces)) {
                        snackbar.showSnackbar(tr("The total charged is %s").format(money(sale.total)))
                    }
                }
                // Auto-print (Settings toggle, default off): fire-and-forget, no blocking, no error
                // dialog - a print failure must never read as the sale having failed.
                r.data?.id?.let { saleId ->
                    if (PrinterPrefs.getAutoPrint(ctx)) {
                        scope.launch { printReceipt(ctx, saleId, snackbar, isAutoPrint = true) }
                    }
                }
                load(); loadCustomers()   // refresh stock + customer balances
            } else snackbar.showSnackbar(r.message ?: tr("Sale failed"))
        // apiFailure (net/ApiErrors.kt): a licensing 403 must say the subscription blocked the sale,
        // never "server unreachable"; a 403 "ask a manager" opens the PIN sheet instead of a toast.
        } catch (e: Exception) {
            val f = apiFailure(e)
            if (f.needsManager && approval == null) askManager() else snackbar.showSnackbar(f.message)
        }
        finally { charging = false }
    }
    fun startCharge() {
        val pm = paymentMethod
        val cust = customer
        val overLim = (listOf(salePct) + cartLines.mapNotNull { it.ownPct }).mapNotNull { overLimit(it, access) }.firstOrNull()
        if (!splitMode && pm == "credit" && cust == null) {
            scope.launch { snackbar.showSnackbar(tr("Select a customer for credit sales (walk-in not allowed)")) }
        } else if (access !is DiscountAccess.None && overLim != null) {
            // Courtesy only: the server refuses an over-limit discount itself (403 DISCOUNT_OVER_LIMIT).
            scope.launch { snackbar.showSnackbar(tr("You can give at most %s%% discount. Ask a manager for more.").format(pctText(overLim))) }
        } else if (needsManager) {
            scope.launch { askManager() }
        } else {
            scope.launch { submitSale(null) }
        }
    }

    // The cart panel: the bottom sheet on a phone, the persistent right-hand pane on a tablet.
    val cartContent: @Composable () -> Unit = {
        run {
            // imePadding() so the redeem-points and down-payment fields --
            // and the Charge button below them -- stay reachable above the
            // keyboard instead of being covered by it. ModalBottomSheet's
            // default windowInsets covers navigationBars only, never ime;
            // imePadding() is additive (0dp while the keyboard is closed) so
            // this does not double-pad that inset.
            //
            // verticalScroll() added (retail-hardware-viewports): this
            // Column's content is NOT bounded -- customer picker, the
            // conditional outstanding-credit line, the whole loyalty-
            // redemption block, the cart lines, subtotal, the payment-method
            // chips, the conditional down-payment field, then Hold sale and
            // Charge -- and ModalBottomSheet does not scroll its content for
            // you (unlike LoginScreen.kt, which already had this and is
            // where this exact bug shape was first found and fixed). With
            // loyaltyEligible true, a credit sale selected (down-payment
            // field visible) and the keyboard open, the combined height
            // comfortably exceeds a typical device's post-IME viewport,
            // which would leave Charge -- the actual money button, on the
            // app's highest-frequency, queue-under-pressure screen --
            // unreachable with no way to recover short of dismissing the
            // keyboard first. The nested cart LazyColumn keeps its own
            // `heightIn(max = 340.dp)` bound, so nesting it inside a
            // scrolling Column here is safe (a bounded-height LazyColumn
            // inside a scrollable Column does not hit Compose's "infinite
            // height" crash -- only an unbounded one would).
            Column(
                Modifier
                    .padding(20.dp)
                    .padding(bottom = 24.dp)
                    .verticalScroll(rememberScrollState())
                    .imePadding(),
            ) {
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
                // Loyalty point redemption (retail-hardware-viewports Android
                // wave; desktop parity, subsystem-retail.js's
                // #pos-loyalty-redeem section, schema v27). Own block, own
                // visibility -- see `loyaltyEligible` above for the four
                // gates. GET .../loyalty is deliberately UNGATED
                // server-side, so a cashier can see the balance before
                // deciding whether to redeem; CAP_DISCOUNT is only enforced
                // when a redemption is actually submitted on checkout.
                if (loyaltyEligible) {
                    Spacer(Modifier.height(12.dp))
                    Text(tr("Loyalty points"), style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.height(4.dp))
                    Text(tr("%d points available").format(loyaltyBalance),
                        style = MaterialTheme.typography.bodyMedium)
                    Spacer(Modifier.height(6.dp))
                    Row(verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedTextField(
                            value = loyaltyRedeemInput, onValueChange = { loyaltyRedeemInput = it },
                            label = { Text(tr("Redeem points")) }, singleLine = true,
                            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                                keyboardType = androidx.compose.ui.text.input.KeyboardType.Number),
                            modifier = Modifier.weight(1f),
                        )
                        // Mirrors _useMaxLoyaltyPoints on the desktop:
                        // min(balance, what THIS sale is worth in points) --
                        // never a request the server would have to clamp.
                        OutlinedButton(onClick = { loyaltyRedeemInput = loyaltyMaxRedeemable.toString() }) {
                            Text(tr("Max"))
                        }
                    }
                    // Surfaces the clamp the MOMENT the typed request exceeds
                    // what will actually apply, instead of leaving the field
                    // showing a number that quietly gets reduced -- before this,
                    // the cashier only learned the true redeemed amount on the
                    // post-sale screen, after the sale had already gone through.
                    // Own row, own visibility -- same conditional-row idiom as
                    // "Redeemed value" right below.
                    if (loyaltyRequestedPoints > loyaltyMaxRedeemable) {
                        Spacer(Modifier.height(4.dp))
                        Text(
                            tr("Only %d points can be applied to this sale").format(loyaltyMaxRedeemable),
                            style = MaterialTheme.typography.bodySmall,
                            color = Warning,
                        )
                    }
                    // Own row, own visibility: shown only once a redemption is
                    // actually previewed (loyaltyValue > 0) -- same
                    // conditional-money-row shape the desktop's
                    // #pos-loyalty-value-row uses. THE CASH TRAP
                    // (retail_api.py's own heading for this): points are not
                    // cash, so this is presented as a reduction of what's
                    // owed, never as a second "Total" -- CartTotalHonestyContractTest
                    // pins that only the server's own `sale.total` may carry
                    // that label.
                    if (loyaltyValue > 0.0) {
                        Spacer(Modifier.height(4.dp))
                        Text(
                            tr("Redeemed value: %s off this sale").format(money(loyaltyValue)),
                            style = MaterialTheme.typography.bodySmall,
                            color = Success,
                        )
                    }
                }
                if (access !is DiscountAccess.None) {
                    Spacer(Modifier.height(12.dp))
                    SaleDiscountField(access = access, text = salePctText, onChange = {
                        salePctText = it
                        // a % beside an amount / target discount is refused by the server: keep the newer intention
                        if (cartDisc != null && clampDiscountPct(parseNum(it)) > 0.0) {
                            cartDisc = null
                            scope.launch { snackbar.showSnackbar(tr("Discount cleared — it cannot be combined with an item discount or promotion")) }
                        }
                    })
                }
                // ANDROID-3 (UI-3a): an amount off / "make the total X" with a reason. Offered to everyone who
                // can sell - a cashier past their limit is asked for a manager's PIN at Charge, not hidden.
                Spacer(Modifier.height(12.dp))
                OutlinedButton(
                    onClick = { showDiscountSheet = true }, enabled = cartLines.isNotEmpty(),
                    modifier = Modifier.fillMaxWidth().height(48.dp),
                ) {
                    Text(
                        if (activeDisc == null) tr("Discount") + " / " + tr("New total")
                        else tr("Discount") + ": " + money(fromMinor(
                            if (activeDisc.kind == CartDiscountKind.AMOUNT) activeDisc.minor else (baseTotalMinor - activeDisc.minor), dpPlaces)) +
                            " (" + activeDisc.reason + ")",
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                }
                Spacer(Modifier.height(12.dp))
                LazyColumn(Modifier.heightIn(max = 340.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    items(cartLines, key = { it.key }) { l ->
                        val p = byId[l.productId] ?: return@items
                        val unit = unitPriceOf(l)
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Box(Modifier.size(40.dp).clip(RoundedCornerShape(10.dp))
                                .background(catColor(p.category_name).copy(alpha = 0.18f)),
                                contentAlignment = Alignment.Center) {
                                Text((p.name ?: "?").take(1).uppercase(), color = catColor(p.category_name),
                                    fontWeight = FontWeight.Bold)
                            }
                            Spacer(Modifier.width(12.dp))
                            Column(Modifier.weight(1f)) {
                                Text(displayName(p), fontWeight = FontWeight.Medium, maxLines = 1,
                                    overflow = TextOverflow.Ellipsis)
                                if (l.modifiers.isNotEmpty()) {
                                    Text(l.modifiers.joinToString(", ") { it.name }, style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
                                }
                                Text(
                                    money(unit) + (if (l.unitPriceOverride != null) "  ·  " + tr("was") + " " + money(lineListPrice(p.sell_price, l)) else ""),
                                    style = MaterialTheme.typography.labelMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                                // Tap for THIS line's own percentage and price (ANDROID-3 line menu). Offered to
                                // everyone who can sell; the % box inside it is gated by `access`.
                                val tag = buildList {
                                    l.ownPct?.let { add("−" + pctText(it) + "%") }
                                    if (l.unitPriceOverride != null) add(tr("Price changed"))
                                }.joinToString(" · ").ifEmpty {
                                    "+ " + (if (access is DiscountAccess.None) tr("Price") else tr("Discount / price"))
                                }
                                Text(
                                    tag,
                                    style = MaterialTheme.typography.labelMedium,
                                    color = MaterialTheme.colorScheme.primary,
                                    modifier = Modifier.clickable(role = Role.Button) { lineMenuKey = l.key }.padding(vertical = 6.dp),
                                )
                            }
                            // No Modifier.size(34.dp) override -- 34dp sits below Material3's
                            // own ~48dp default and the 44dp accessibility floor the desktop
                            // client enforces (--touch-target-min). The cart is the
                            // highest-frequency tap surface in the app; every other icon
                            // button here already relies on the Material default.
                            FilledTonalIconButton(onClick = { cartLines = decrementLine(cartLines, l.key) }) {
                                Icon(Icons.Default.Remove, tr("Decrease quantity"))
                            }
                            Text(fmtQty(l.quantity), fontWeight = FontWeight.Bold,
                                modifier = Modifier.widthIn(min = 28.dp), textAlign = androidx.compose.ui.text.style.TextAlign.Center)
                            FilledTonalIconButton(onClick = {
                                val out = incrementLine(cartLines, l.key, p.total_stock)
                                if (out.accepted) cartLines = out.lines
                                else scope.launch { snackbar.showSnackbar(tr("Max stock: %s").format(fmtQty(p.total_stock))) }
                            }) { Icon(Icons.Default.Add, tr("Increase quantity")) }
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
                if (activeDisc != null) {
                    // The phone's guess at what the shop will charge, labelled as one; the real total
                    // comes back with the sale (and replaces this the moment it does).
                    Spacer(Modifier.height(8.dp))
                    Row {
                        Text(tr("Estimated total"), style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
                        Text(money(fromMinor(estimateMinor, dpPlaces)), style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold)
                    }
                    Text(tr("An estimate. The shop confirms the real total when you charge."),
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
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
                if (paymentMethod == "credit" && !splitMode) {
                    Spacer(Modifier.height(8.dp))
                    OutlinedTextField(downPayment, { downPayment = it },
                        label = { Text(tr("Paid now (optional) — rest goes on credit")) }, singleLine = true,
                        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                            keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal),
                        modifier = Modifier.fillMaxWidth())
                }
                Spacer(Modifier.height(8.dp))
                TextButton(onClick = { splitMode = !splitMode; splitParts = emptyList(); splitAmountText = "" }) {
                    Text(if (splitMode) tr("Pay with one method") else tr("Split payment"))
                }
                if (splitMode) {
                    val methodName = payOptions.firstOrNull { it.second == paymentMethod }?.first ?: paymentMethod
                    SplitPaymentPanel(
                        methodLabel = tr(methodName),
                        estimateMinor = dueMinor,
                        parts = splitParts,
                        amountText = splitAmountText,
                        summary = splitSummary,
                        labelFor = { m -> tr(payOptions.firstOrNull { it.second == m }?.first ?: m.replaceFirstChar { it.uppercase() }) },
                        onAmountChange = { splitAmountText = it },
                        onFillRemaining = { splitAmountText = remainingInput(splitSummary) },
                        onAdd = {
                            val minor = parseAmountMinor(splitAmountText, dpPlaces)
                            if (minor == null) scope.launch { snackbar.showSnackbar(tr("Enter a valid amount")) }
                            else { splitParts = addTenderPart(splitParts, paymentMethod, minor); splitAmountText = "" }
                        },
                        onRemove = { i -> splitParts = removeTenderPart(splitParts, i) },
                    )
                }
                Spacer(Modifier.height(16.dp))
                OutlinedButton(onClick = { showHoldDialog = true }, enabled = RetailSession.hasAnyCapability(CAPS_HOLD_SALE),
                    modifier = Modifier.fillMaxWidth().height(48.dp)) { Text(tr("Hold sale (park for later)")) }
                Spacer(Modifier.height(8.dp))
                Button(
                    onClick = { startCharge() },
                    enabled = !charging && cartLines.isNotEmpty() && (!splitMode || (splitParts.size >= 2 && splitSummary.chargeable)),
                    modifier = Modifier.fillMaxWidth().height(54.dp),
                ) {
                    if (charging) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp,
                        color = MaterialTheme.colorScheme.onPrimary)
                    // NET OF REDEEMED POINTS. This is the figure a cashier
                    // reads aloud as "that'll be ...", so it has to be what
                    // the customer actually hands over. It used to render the
                    // raw `total`, which ignored `loyaltyValue` entirely --
                    // so on every redemption the button contradicted the
                    // "Redeemed value: X off this sale" line sitting directly
                    // above it, and quoted the customer too much.
                    //
                    // The Subtotal row above deliberately keeps showing the
                    // PRE-points `total`: it is captioned "Tax and discounts
                    // are applied at checkout" and is the basket's own value.
                    // Only this button claims to be what is owed now.
                    //
                    // Still a local preview -- the server remains the only
                    // pricing authority (docs/architecture/financial-authority-
                    // contracts.md), which is exactly why this stays labelled
                    // "Charge" and never "Total". CartTotalHonestyContractTest
                    // pins that distinction.
                    else Text(tr("Charge") + "  " + (if (splitMode) money(fromMinor(splitSummary.enteredMinor - splitSummary.changeMinor, dpPlaces))
                                                        else money(((if (activeDisc != null) fromMinor(estimateMinor, dpPlaces) else total) - loyaltyValue).coerceAtLeast(0.0))),
                        style = MaterialTheme.typography.labelLarge)
                }
            }
        }
    }

    Box(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxSize()) {
        Column(Modifier.weight(1f).fillMaxHeight()) {
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
                if (heldCount > 0) {
                    FilledTonalButton(onClick = { showHeld = true; scope.launch { refreshHeld() } }) {
                        Text(tr("Held") + " $heldCount")
                    }
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
                    contentPadding = PaddingValues(12.dp, 0.dp, 12.dp, if (wide) 12.dp else 110.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                    modifier = Modifier.weight(1f),
                ) {
                    items(filtered, key = { it.id }) { p ->
                        ProductTile(p,
                            inCart = cartLines.filter { it.productId == p.id || byId[it.productId]?.parent_product_id == p.id }.sumOf { it.quantity }.toInt(),
                            variantCount = variantCounts[p.id] ?: 0,
                            onAdd = { if (choose(p) == Chosen.NO_STOCK) scope.launch { snackbar.showSnackbar(tr("Only %s in stock").format(fmtQty(p.total_stock))) } })
                    }
                }
            }
        }
        // Tablet / large window: the cart and Charge stay on screen beside the products.
        if (wide) {
            VerticalDivider()
            Surface(Modifier.width(400.dp).fillMaxHeight(), color = MaterialTheme.colorScheme.surface) { cartContent() }
        }
        }

        // Animated floating cart bar (phone only: on a tablet the cart pane is always there)
        AnimatedVisibility(
            visible = count > 0 && !wide,
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
            PaymentSuccess(sale = successSale ?: com.actionaura.retail.net.SaleResult(), onNewSale = { successSale = null }, snackbar = snackbar)
        }
    }

    if (showCart && !wide) {
        val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
        ModalBottomSheet(onDismissRequest = { showCart = false }, sheetState = sheet) { cartContent() }
    }

    lineMenuKey?.let { key ->
        val line = cartLines.firstOrNull { it.key == key }
        if (line == null) lineMenuKey = null else LineMenuDialog(
            productName = displayName(byId[line.productId]), line = line, listPrice = listPriceOf(line),
            access = access, holdsDiscountCapability = RetailSession.hasCapability(CAP_DISCOUNT), dp = dpPlaces,
            onApply = { out ->
                cartLines = setPriceOverride(setOwnPct(cartLines, key, out.ownPct), key, out.unitPriceOverride, out.priceReason)
                // an own % or price beside an amount / target discount is refused by the server
                if (cartDisc != null && (out.ownPct != null)) {
                    cartDisc = null
                    scope.launch { snackbar.showSnackbar(tr("Discount cleared — it cannot be combined with an item discount or promotion")) }
                }
                lineMenuKey = null
            },
            onDismiss = { lineMenuKey = null },
        )
    }

    if (showDiscountSheet) {
        CartDiscountSheet(
            estimateLines = estLines, grossSubtotalMinor = grossMinor, baseTotalMinor = baseTotalMinor, dp = dpPlaces,
            current = activeDisc,
            blockedBy = if (salePct > 0.0 || cartLines.any { it.ownPct != null }) tr("Remove the item discounts and promotions first") else null,
            onApply = { kind, minor, reason -> cartDisc = CartDiscount(kind, minor, reason, cartSig); showDiscountSheet = false },
            onRemove = { cartDisc = null; showDiscountSheet = false },
            onDismiss = { showDiscountSheet = false },
        )
    }

    approvers?.let { list ->
        ManagerApprovalSheet(
            approvers = list,
            onApprove = { req -> approvers = null; scope.launch { submitSale(req) } },
            onDismiss = { approvers = null },
        )
    }

    variantParent?.let { parent ->
        VariantPickerSheet(
            parentName = parent.name ?: "—", variants = variantList,
            onPick = { v ->
                variantParent = null; variantList = null
                if (choose(v) == Chosen.NO_STOCK) scope.launch { snackbar.showSnackbar(tr("Only %s in stock").format(fmtQty(v.total_stock))) }
            },
            onOutOfStock = { scope.launch { snackbar.showSnackbar(tr("Out of stock")) } },
            onDismiss = { variantParent = null; variantList = null },
        )
    }

    modifierFor?.let { (product, groups) ->
        ModifierSheet(
            product = product, groups = groups,
            onAdd = { ids, picks ->
                modifierFor = null
                if (!addPlain(product, ids, picks)) scope.launch { snackbar.showSnackbar(tr("Only %s in stock").format(fmtQty(product.total_stock))) }
            },
            onDismiss = { modifierFor = null },
        )
    }

    if (showHoldDialog) {
        HoldDialog(busy = holdBusy, onHold = { note -> scope.launch { holdCurrent(note) } }, onDismiss = { showHoldDialog = false })
    }
    if (showHeld) {
        HeldSalesSheet(
            rows = heldRows,
            onResume = { h -> if (cartLines.isNotEmpty()) pendingResume = h else scope.launch { resumeHeld(h) } },
            onDiscard = { h -> pendingDiscard = h },
            onDismiss = { showHeld = false },
        )
    }
    pendingResume?.let { h ->
        ConfirmDialog(
            title = tr("Resume Held Sale"),
            message = tr("Your current cart is not empty. Resuming will replace it with the held sale. Continue?"),
            confirmLabel = tr("Resume"),
            onConfirm = { pendingResume = null; scope.launch { resumeHeld(h) } },
            onDismiss = { pendingResume = null },
        )
    }
    pendingDiscard?.let { h ->
        ConfirmDialog(
            title = tr("Discard Held Sale"),
            message = tr("Discard this held sale? This cannot be undone."),
            confirmLabel = tr("Discard"),
            onConfirm = { pendingDiscard = null; scope.launch { discardHeld(h) } },
            onDismiss = { pendingDiscard = null },
        )
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
                            val c = choose(p)
                            lastScanned = scanStatus(p, c)
                            // A picker is a sheet in the main window, behind the camera: close the camera so it can be seen.
                            if (c == Chosen.PICKER) { showScanner = false; lastScanned = null }
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

/** What choosing a product did: it went in, the stock guard refused it, or a picker opened first. */
private enum class Chosen { ADDED, NO_STOCK, PICKER }

private fun scanStatus(p: Product, c: Chosen): String = when (c) {
    Chosen.ADDED -> "✓ ${p.name}"
    Chosen.NO_STOCK -> "✗ ${p.name}: " + tr("Only %s in stock").format(fmtQty(p.total_stock))
    Chosen.PICKER -> "… ${p.name}"
}

/** Half of the currency's smallest unit: the difference below which two amounts are the same amount. */
private fun halfMinorUnit(dp: Int): Double = 0.5 / Math.pow(10.0, dp.toDouble())

@Composable
private fun ProductTile(p: Product, inCart: Int, variantCount: Int, onAdd: () -> Unit) {
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
            if (variantCount > 0) {
                // A parent's own stock says nothing; what the cashier needs to know is that it opens a picker.
                Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(20.dp),
                    modifier = Modifier.align(Alignment.BottomStart).padding(6.dp)) {
                    Text(tr("%d variants").format(variantCount), style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp))
                }
            } else StockBadge(stock, Modifier.align(Alignment.BottomStart).padding(6.dp))
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
private fun PaymentSuccess(sale: com.actionaura.retail.net.SaleResult, onNewSale: () -> Unit, snackbar: SnackbarHostState) {
    val check = remember { Animatable(0f) }
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val scope = rememberCoroutineScope()
    var printing by remember { mutableStateOf(false) }
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
            // Server truth, not the request: `points_redeemed`/
            // `points_redeemed_amount` are what create_sale ACTUALLY applied
            // (retail_api.py's own clamp may have honoured fewer points than
            // requested -- the sale total, or the ledger balance, could have
            // left less room than the preview assumed), so this reads the
            // response verbatim, the same rule every other figure on this
            // screen already follows.
            if (sale.points_redeemed_amount > 0.0) {
                Spacer(Modifier.height(4.dp))
                Text(
                    tr("%d points redeemed (%s)").format(sale.points_redeemed, money(sale.points_redeemed_amount)),
                    style = MaterialTheme.typography.labelMedium,
                    color = Success,
                )
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
            // Only offered once a network printer is actually configured
            // (Settings -> Receipt Printer) -- an install that never sets one
            // up sees no new button here at all, matching this codebase's
            // "costs nothing to an install that doesn't use it" rule.
            if (PrinterPrefs.isConfigured(ctx)) {
                Spacer(Modifier.height(12.dp))
                OutlinedButton(
                    onClick = { scope.launch { printing = true; printReceipt(ctx, sale.id, snackbar); printing = false } },
                    enabled = !printing,
                    modifier = Modifier.fillMaxWidth().height(54.dp),
                ) {
                    if (printing) {
                        CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                    } else {
                        Icon(Icons.Default.Print, null); Spacer(Modifier.width(8.dp)); Text(tr("Print Receipt"))
                    }
                }
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

// Receipt printing: asks the server for the receipt as layout data, DRAWS it as a
// 1-bit bitmap on the phone (so Arabic prints) and sends it to whichever printer
// Settings -> Receipt Printer chose (built-in, Bluetooth, USB or network) --
// see printer/ReceiptPrintService.kt. Both the manual "Print Receipt" button
// and auto-print (PosScreen's sale-success branch) call this one function, so
// there is only one place that can get "what does printing a receipt mean" wrong.
//
// `isAutoPrint` is the drawer rule's input: ONLY the print that follows a sale
// may open the drawer (and then only if the owner switched auto-open on AND the
// server says this sale was cash) -- a manual reprint never kicks, because
// opening the drawer is a deliberate act, not a side effect of printing a copy.
//
// Never lets a print failure look like the sale failed: this is only ever
// called with a sale id from an already-successful, server-issued sale
// response, and PaymentSuccess exists to CONFIRM that sale, not to gate
// it. So every failure path here ends in a snackbar, exactly like the
// checkout screen's own error handling above -- never a thrown exception,
// never a dialog that could read as "the sale didn't go through."
private suspend fun printReceipt(
    ctx: android.content.Context, saleId: Int, snackbar: SnackbarHostState, isAutoPrint: Boolean = false,
) {
    ReceiptPrintService.printSale(ctx, saleId, isAutoPrint)
        .onFailure { e -> snackbar.showSnackbar(e.message ?: tr("Couldn't print receipt")) }
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
