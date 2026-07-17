package com.actionaura.retail.barcode

/**
 * Pure duplicate-scan suppression logic for continuous barcode scanning,
 * extracted out of BarcodeScanner.kt's CameraX analyzer callback (Phase 4F)
 * so it can be unit-tested without a camera or device -- the physical
 * camera/CameraX/ML Kit wiring itself still requires real-device
 * validation and is not claimed to be covered by this.
 *
 * Same semantics as the inline check it replaced: the same code scanned
 * again within [windowMs] of the previous accepted scan is suppressed;
 * a different code, or the same code after the window has elapsed, is not.
 */
fun isDuplicateScan(code: String, lastCode: String?, lastAt: Long, now: Long, windowMs: Long = 1500L): Boolean =
    code == lastCode && (now - lastAt) < windowMs
