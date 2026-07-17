package com.actionaura.retail.barcode

import com.actionaura.retail.net.Product

/**
 * Pure barcode-to-product matching, extracted out of PosScreen's scanner
 * result handler (Phase 4F) for unit testability. Matches a scanned code
 * against either a product's barcode or its SKU, case-insensitively --
 * identical semantics to the inline check it replaced in RetailScreens.kt.
 */
fun findProductByCode(products: List<Product>, code: String): Product? =
    products.firstOrNull {
        it.barcode?.equals(code, ignoreCase = true) == true ||
            it.sku?.equals(code, ignoreCase = true) == true
    }
