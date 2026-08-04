package com.actionaura.retail.data

import com.actionaura.retail.financial.FinancialResult
import com.actionaura.retail.financial.FinalizedSaleSnapshot
import com.actionaura.retail.financial.FinalizedReturnSnapshot
import com.actionaura.retail.usecases.FinalizeReturnCommand
import com.actionaura.retail.usecases.FinalizeSaleCommand

/**
 * M3.4 -- the shared transactional service boundary for sale finalization.
 * Mirrors create_sale()'s real, exact transaction shape (see
 * docs/retail/unified_mobile/transaction-boundary-audit.md): a single
 * atomic operation that (1) checks idempotency, (2) resolves+validates
 * every line against a lock-protected fresh read of product/stock state,
 * (3) computes via the pure financial engine, (4) persists everything --
 * sale, lines, stock mutation, ledger entries -- or rolls back all of it
 * on any failure.
 *
 * This interface intentionally does NOT expose separate
 * read-product/read-stock/write-sale methods for the use-case layer to
 * orchestrate -- the real Python authority's oversell-race protection
 * depends on validation happening on a lock-fresh read taken atomically
 * with the write (BEGIN IMMEDIATE, then validate, then write, all inside
 * one transaction). Splitting that into separate calls from outside the
 * repository would reintroduce exactly the TOCTOU race the real
 * implementation was hardened against (AUDIT-009). Implementations (the
 * in-memory test double now, a real SQLite-backed implementation in
 * Milestone 4) own the whole atomic operation.
 */
interface SaleRepository {
    suspend fun finalizeSale(command: FinalizeSaleCommand): FinancialResult<FinalizedSaleSnapshot>
}

interface ReturnRepository {
    suspend fun finalizeReturn(command: FinalizeReturnCommand): FinancialResult<FinalizedReturnSnapshot>
}

/** What a sale-finalization implementation needs to resolve a line -- the shared, trusted product-catalog read shape (never client-supplied). */
data class ProductForSale(
    val productId: String,
    val name: String,
    val sellPriceRaw: String,
    val taxRateRaw: Double,
    val isActive: Boolean,
)
