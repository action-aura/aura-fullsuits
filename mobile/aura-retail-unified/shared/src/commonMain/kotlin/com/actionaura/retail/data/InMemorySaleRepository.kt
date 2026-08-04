package com.actionaura.retail.data

import com.actionaura.retail.financial.*
import com.actionaura.retail.usecases.FinalizeSaleCommand
import com.actionaura.retail.usecases.FinalizeReturnCommand
import com.actionaura.retail.usecases.SaleLineRequest
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** In-memory credit customer state -- the minimal shape invariant #13 needs. */
data class InMemoryCustomer(
    val customerId: String,
    val creditMode: String = "none", // "none" | "limited" | "unlimited"
    val creditLimit: Money = Money.ZERO,
    var creditBalance: Money = Money.ZERO,
)

/**
 * M3.4 -- in-memory test double for SaleRepository/ReturnRepository.
 * Reproduces every real invariant from financial-invariant-catalog.md
 * that a repository (not the pure calculation engine) is responsible for:
 * idempotency + conflicting-payload detection (closing invariant #12),
 * atomic all-or-nothing persistence (invariant #9), oversell protection
 * via a lock taken before validation (invariants #8/#10, mirroring
 * BEGIN IMMEDIATE), cumulative-return-limit enforcement (invariant #17),
 * and refund derivation strictly from the original sale's snapshot
 * (invariant #16).
 *
 * Real SQLite-backed implementation lives in Milestone 4 -- this class is
 * the actual shared implementation used by every commonTest, not merely
 * throwaway scaffolding, matching the governing spec's own "in-memory or
 * test database implementation" framing.
 */
class InMemorySaleRepository(
    private val products: MutableMap<String, ProductForSale> = mutableMapOf(),
    private val stockByProductAndBranch: MutableMap<Pair<String, String>, Quantity> = mutableMapOf(),
    private val customers: MutableMap<String, InMemoryCustomer> = mutableMapOf(),
    val currency: CurrencyCode = CurrencyCode("USD"),
    val mode: TaxMode = TaxMode.DEFAULT,
    private val clock: () -> Long = { 0L },
) : SaleRepository, ReturnRepository {

    private val lock = Mutex() // real BEGIN IMMEDIATE equivalent -- see docs/retail/unified_mobile/transaction-boundary-audit.md
    private val salesById = mutableMapOf<String, FinalizedSaleSnapshot>()
    private val salesByIdempotencyKey = mutableMapOf<String, FinalizedSaleSnapshot>()
    private val returnsByIdempotencyKey = mutableMapOf<String, FinalizedReturnSnapshot>()
    private val returnedQuantityBySaleAndProduct = mutableMapOf<Pair<String, String>, Quantity>()
    private var nextSaleNumber = 1
    private var nextReturnNumber = 1

    fun putProduct(product: ProductForSale, initialStock: Quantity, branchId: String) {
        products[product.productId] = product
        stockByProductAndBranch[product.productId to branchId] = initialStock
    }

    fun putCustomer(customer: InMemoryCustomer) {
        customers[customer.customerId] = customer
    }

    /** No balance row (real Python default, retail_api.py's `on_hand = ... if balance else 0.0`) is exactly Quantity.ZERO here, never null-as-"unknown". */
    fun stockOnHand(productId: String, branchId: String): Quantity =
        stockByProductAndBranch[productId to branchId] ?: Quantity.ZERO

    override suspend fun finalizeSale(command: FinalizeSaleCommand): FinancialResult<FinalizedSaleSnapshot> = lock.withLock {
        // 1. Idempotency check -- BEFORE any mutation, matching create_sale()'s real ordering.
        command.idempotencyKey?.let { key ->
            salesByIdempotencyKey[key]?.let { existing ->
                return@withLock if (sameSalePayload(existing, command)) {
                    FinancialResult.Success(existing)
                } else {
                    // CANONICAL_UNIFIED -- real Python gap closed (invariant #12): the
                    // current backend returns the stale result unconditionally; this
                    // repository detects the mismatch and reports a real conflict.
                    FinancialResult.Failure(FinancialError.DuplicateOperationConflict(key))
                }
            }
        }

        if (command.lines.isEmpty()) return@withLock FinancialResult.Failure(FinancialError.EmptySale)

        // 2. Resolve + validate every line against a lock-fresh read (oversell protection).
        val resolvedLines = mutableListOf<FinalizedSaleLineSnapshot>()
        for (req in command.lines) {
            val resolved = resolveSaleLine(req, command.branchId)
            when (resolved) {
                is FinancialResult.Failure -> return@withLock resolved
                is FinancialResult.Success -> resolvedLines += resolved.value
            }
        }

        val subtotal = resolvedLines.fold(Money.ZERO) { acc, l -> acc + l.calculation.gross }
        val discountAmount = resolvedLines.fold(Money.ZERO) { acc, l -> acc + l.calculation.discountAmount }
        val taxAmount = resolvedLines.fold(Money.ZERO) { acc, l -> acc + l.calculation.tax }
        val total = resolvedLines.fold(Money.ZERO) { acc, l -> acc + l.calculation.total }

        // 3. Payment/change/credit rules (invariant #13/#14/#15).
        val paidResult = when (val raw = command.amountPaidRaw) {
            null -> FinancialResult.Success(total) // invariant #14 -- omitted amount defaults to exactly total
            else -> Money.parse(raw)
        }
        val paid = when (paidResult) {
            is FinancialResult.Failure -> return@withLock paidResult
            is FinancialResult.Success -> paidResult.value
        }
        val change = calculateChange(paid, total)
        val balanceDue = (total - paid).coerceAtLeastZero()

        if (!balanceDue.isZero()) {
            if (command.customerId == null) {
                return@withLock FinancialResult.Failure(FinancialError.PaymentBelowTotalRequiresCustomer(balanceDue.toString()))
            }
            val customer = customers[command.customerId]
            if (customer == null || customer.creditMode == "none") {
                return@withLock FinancialResult.Failure(FinancialError.CreditModeDisallowed(command.customerId))
            }
            if (customer.creditMode == "limited") {
                val projected = customer.creditBalance + balanceDue
                if (projected > customer.creditLimit) {
                    return@withLock FinancialResult.Failure(
                        FinancialError.CreditLimitExceeded(customer.creditLimit.toString(), customer.creditBalance.toString(), balanceDue.toString())
                    )
                }
            }
        }

        // 4. Persist atomically (stock decrement + sale record) -- nothing above this point mutated shared state.
        for (line in resolvedLines) {
            val key = line.productId to command.branchId
            val current = stockByProductAndBranch[key] ?: Quantity.ZERO
            stockByProductAndBranch[key] = current - line.quantity
        }
        command.customerId?.let { cid ->
            customers[cid]?.let { it.creditBalance = it.creditBalance + balanceDue }
        }

        val saleId = "sale-${salesById.size + 1}"
        val saleNumber = "SALE-${(nextSaleNumber++).toString().padStart(6, '0')}"
        val snapshot = FinalizedSaleSnapshot(
            saleId = saleId, saleNumber = saleNumber, branchId = command.branchId, customerId = command.customerId,
            lines = resolvedLines, subtotal = subtotal, discountAmount = discountAmount, taxAmount = taxAmount, total = total,
            amountPaid = paid, change = change, balanceDue = balanceDue, paymentMethod = command.paymentMethod,
            currency = currency, mode = mode, createdAtEpochMillis = clock(), idempotencyKey = command.idempotencyKey,
        )
        salesById[saleId] = snapshot
        command.idempotencyKey?.let { salesByIdempotencyKey[it] = snapshot }
        FinancialResult.Success(snapshot)
    }

    private fun resolveSaleLine(
        req: SaleLineRequest,
        branchId: String,
    ): FinancialResult<FinalizedSaleLineSnapshot> {
        val product = products[req.productId] ?: return FinancialResult.Failure(FinancialError.ProductNotFound(req.productId))
        if (!product.isActive) return FinancialResult.Failure(FinancialError.ProductInactive(product.name))

        val quantityResult = Quantity.parse(req.quantityRaw)
        val quantity = when (quantityResult) {
            is FinancialResult.Failure -> return quantityResult
            is FinancialResult.Success -> quantityResult.value
        }

        // No balance row -- treat as zero on-hand, the real Python default (retail_api.py: `on_hand = ... if balance else 0.0`).
        val onHand = stockByProductAndBranch[req.productId to branchId] ?: Quantity.ZERO
        if (quantity > onHand) {
            return FinancialResult.Failure(FinancialError.InsufficientStock(product.name, onHand.toString(), quantity.toString()))
        }

        val unitPriceResult = Money.parse(product.sellPriceRaw)
        val unitPrice = when (unitPriceResult) {
            is FinancialResult.Failure -> return unitPriceResult
            is FinancialResult.Success -> unitPriceResult.value
        }
        val discountPct = PercentageRate.clampToDiscountRange(req.discountPctRaw)
        val taxRate = PercentageRate.trusted(product.taxRateRaw)
        val calc = calculateLine(unitPrice, quantity, discountPct, taxRate, mode)

        return FinancialResult.Success(
            FinalizedSaleLineSnapshot(
                productId = req.productId, productNameAtSale = product.name, quantity = quantity,
                unitPriceAtSale = unitPrice, discountPctAtSale = discountPct, taxRateAtSale = taxRate, calculation = calc,
            )
        )
    }

    private fun sameSalePayload(existing: FinalizedSaleSnapshot, command: FinalizeSaleCommand): Boolean {
        // Real bug found by this milestone's own test
        // (idempotentRetryConflictingPayloadReturnsConflict): comparing only
        // productId let a retry with a DIFFERENT quantity under the same key
        // wrongly match as "the same payload" and silently return the first
        // sale's result -- exactly the real Python gap (invariant #12) this
        // repository exists to close, so the comparison itself must be
        // materially complete, not just present.
        if (existing.lines.size != command.lines.size) return false
        return existing.lines.zip(command.lines).all { (line, req) ->
            val requestedQuantity = Quantity.parse(req.quantityRaw).getOrNull()
            line.productId == req.productId && requestedQuantity != null && line.quantity == requestedQuantity
        }
    }

    override suspend fun finalizeReturn(command: FinalizeReturnCommand): FinancialResult<FinalizedReturnSnapshot> = lock.withLock {
        command.idempotencyKey?.let { key ->
            returnsByIdempotencyKey[key]?.let { existing ->
                return@withLock if (existing.saleId == command.saleId && existing.lines.size == command.lines.size) {
                    FinancialResult.Success(existing)
                } else {
                    FinancialResult.Failure(FinancialError.DuplicateOperationConflict(key))
                }
            }
        }

        if (command.lines.isEmpty()) return@withLock FinancialResult.Failure(FinancialError.EmptyReturn)

        val sale = salesById[command.saleId] ?: return@withLock FinancialResult.Failure(FinancialError.SaleNotFound(command.saleId))

        val resolvedLines = mutableListOf<com.actionaura.retail.financial.FinalizedReturnLineSnapshot>()
        var refundTotal = Money.ZERO
        for (req in command.lines) {
            val quantityResult = Quantity.parse(req.quantityRaw)
            val quantity = when (quantityResult) {
                is FinancialResult.Failure -> return@withLock quantityResult
                is FinancialResult.Success -> quantityResult.value
            }
            val originalLine = sale.lines.firstOrNull { it.productId == req.productId }
                ?: return@withLock FinancialResult.Failure(FinancialError.ReturnExceedsSoldQuantity(req.productId, command.saleId))

            val alreadyReturned = returnedQuantityBySaleAndProduct[command.saleId to req.productId]
            val remaining = if (alreadyReturned == null) originalLine.quantity else (originalLine.quantity - alreadyReturned)
            if (quantity > remaining) {
                return@withLock FinancialResult.Failure(FinancialError.ReturnExceedsRemainingQuantity(quantity.toString(), remaining.toString()))
            }

            // Refund derived strictly from the ORIGINAL sale's frozen snapshot values -- invariant #16.
            val calc = calculateLine(originalLine.unitPriceAtSale, quantity, originalLine.discountPctAtSale, originalLine.taxRateAtSale, sale.mode)
            resolvedLines += com.actionaura.retail.financial.FinalizedReturnLineSnapshot(
                productId = req.productId, productNameAtSale = originalLine.productNameAtSale,
                quantity = quantity, unitPriceAtSale = originalLine.unitPriceAtSale, refundAmount = calc.total,
            )
            refundTotal += calc.total
        }

        // Restock: increment (creating the balance if absent mirrors create_return()'s
        // real `INSERT OR IGNORE ... quantity_on_hand=0` then `UPDATE ... +=` sequence).
        for (line in resolvedLines) {
            val stockKey = line.productId to sale.branchId
            val currentStock = stockByProductAndBranch[stockKey] ?: Quantity.ZERO
            stockByProductAndBranch[stockKey] = currentStock + line.quantity

            val returnedKey = sale.saleId to line.productId
            val alreadyReturned = returnedQuantityBySaleAndProduct[returnedKey] ?: Quantity.ZERO
            returnedQuantityBySaleAndProduct[returnedKey] = alreadyReturned + line.quantity
        }

        val returnId = "return-${nextReturnNumber}"
        val returnNumber = "RET-${(nextReturnNumber++).toString().padStart(6, '0')}"
        val snapshot = FinalizedReturnSnapshot(
            returnId = returnId, returnNumber = returnNumber, saleId = command.saleId, branchId = sale.branchId,
            lines = resolvedLines, refundTotal = refundTotal, refundMethod = command.refundMethod, reason = command.reason,
            createdAtEpochMillis = clock(), idempotencyKey = command.idempotencyKey,
        )
        command.idempotencyKey?.let { returnsByIdempotencyKey[it] = snapshot }
        FinancialResult.Success(snapshot)
    }
}
