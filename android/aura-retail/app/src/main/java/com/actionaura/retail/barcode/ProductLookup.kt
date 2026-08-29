package com.actionaura.retail.barcode

import com.actionaura.retail.net.AuraApi
import com.actionaura.retail.net.Product
import kotlinx.coroutines.CancellationException
import retrofit2.HttpException
import java.io.IOException

/**
 * Pure barcode-to-product matching, extracted out of PosScreen's scanner
 * result handler (Phase 4F) for unit testability. Matches a scanned code
 * against either a product's barcode or its SKU, case-insensitively --
 * identical semantics to the inline check it replaced in RetailScreens.kt.
 *
 * RETIRED FROM THE PRODUCTION SCAN PATH (launch-readiness, "the POS scale
 * fix"). A hypermarket-scale catalogue makes an in-memory linear scan of the
 * FULLY fetched products() list exactly the thing a scan should never have
 * had to download in the first place -- see [lookupProductByCode] below,
 * which PosScreen's HID and camera scan handlers (RetailScreens.kt) now
 * call instead, mirroring the desktop/web fix (dfc0ef0/17efa5b).
 *
 * Kept, unchanged, because [ProductLookupTest] still exercises it and
 * deleting it would delete that coverage rather than retire it. Nothing in
 * this module calls it in production anymore, so its tests now prove only
 * that THIS pure matcher's semantics are correct -- they can no longer
 * prove anything about what a live scan actually resolves to; that
 * guarantee moved to ProductLookupResultTest, against [lookupProductByCode].
 */
fun findProductByCode(products: List<Product>, code: String): Product? =
    products.firstOrNull {
        it.barcode?.equals(code, ignoreCase = true) == true ||
            it.sku?.equals(code, ignoreCase = true) == true
    }

/**
 * Outcome of resolving a scanned code against the server (AuraApi.productLookup).
 * Three outcomes, not two -- a scan handler has to tell "no such product" apart
 * from "the lookup itself failed": conflating them tells a cashier an item does
 * not exist when the real problem is the connection, unacceptable in a product
 * built to keep selling while the network is down. Modelled as a closed
 * hierarchy (see ActivationOutcome in licensing/LicensingMessages.kt for the
 * same pattern in this module) so the call site's `when` is exhaustive and a
 * new outcome can't be added without the UI being forced to decide what it
 * does about it. Mirrors the web client's `{product, error}` pair in shape
 * (subsystem-retail.js's _findByCode).
 */
sealed interface ProductLookupResult {
    /** A real match. */
    data class Found(val product: Product) : ProductLookupResult

    /** The server said "no such product" (404) -- a normal outcome for a till. */
    data object NotFound : ProductLookupResult

    /**
     * The lookup itself failed -- offline, a non-404 HTTP error, or anything
     * else the suspend call threw. Never rendered as "not found": the raw
     * [cause] is handed back so the call site can format it with
     * com.actionaura.retail.net.apiErrorMessage, the same mapping every other
     * screen's "call failed" catch block already uses (so a licensing 403
     * reads as a subscription problem, not a fabricated "item doesn't exist").
     */
    data class Failed(val cause: Throwable) : ProductLookupResult
}

/**
 * Resolves ONE scanned code against the embedded server instead of the fully
 * fetched products() list. The backend route (GET /api/sub/retail/products/
 * lookup?code=, retail_api.py's lookup_product()) is company-scoped,
 * index-backed (barcode then SKU, schema v21) and case-insensitive -- so
 * this sends [code] through verbatim, with no client-side case folding of
 * its own, to preserve exactly the case-insensitive semantics
 * [findProductByCode] used to provide locally.
 *
 * A 404 (retrofit2.HttpException, code 404) is [ProductLookupResult.NotFound].
 * Everything else the call can throw -- IOException (unreachable/offline), any
 * other HttpException, a malformed body -- is [ProductLookupResult.Failed].
 * CancellationException is rethrown, never wrapped: a cancelled scan is not a
 * lookup failure to report to the cashier.
 */
suspend fun lookupProductByCode(api: AuraApi, code: String): ProductLookupResult {
    val product = try {
        api.productLookup(code).data
    } catch (e: CancellationException) {
        throw e
    } catch (e: HttpException) {
        return if (e.code() == 404) ProductLookupResult.NotFound else ProductLookupResult.Failed(e)
    } catch (e: IOException) {
        return ProductLookupResult.Failed(e)
    } catch (e: Exception) {
        return ProductLookupResult.Failed(e)
    }
    // A 200 with no `data` is a malformed response, not "not found" -- the
    // server never sends this shape (see ProductLookupResponse's doc
    // comment), but a suspend function must still account for it rather
    // than NPE on the caller.
    return product?.let { ProductLookupResult.Found(it) }
        ?: ProductLookupResult.Failed(IllegalStateException("Lookup returned status success with no product body"))
}
