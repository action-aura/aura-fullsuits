package com.actionaura.retail.barcode

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Phase 4F: unit tests for the exact duplicate-scan logic BarcodeScanner.kt
 * calls (extracted, not reimplemented -- see BarcodeDebounce.kt). Covers
 * only the debounce decision itself; CameraX/ML Kit/permission behavior
 * requires a real device and is not exercised here. */
class BarcodeDebounceTest {

    @Test
    fun repeated_same_code_within_window_is_suppressed() {
        assertTrue(isDuplicateScan("123456", lastCode = "123456", lastAt = 1000L, now = 1500L))
    }

    @Test
    fun same_code_after_window_elapsed_is_not_suppressed() {
        assertFalse(isDuplicateScan("123456", lastCode = "123456", lastAt = 1000L, now = 2600L))
    }

    @Test
    fun different_code_is_never_suppressed_even_immediately() {
        assertFalse(isDuplicateScan("999999", lastCode = "123456", lastAt = 1000L, now = 1001L))
    }

    @Test
    fun first_scan_with_no_prior_code_is_not_suppressed() {
        assertFalse(isDuplicateScan("123456", lastCode = null, lastAt = 0L, now = 500L))
    }

    @Test
    fun exactly_at_window_boundary_is_not_suppressed() {
        // (now - lastAt) < windowMs -- exactly equal to the window is NOT a duplicate.
        assertFalse(isDuplicateScan("123456", lastCode = "123456", lastAt = 1000L, now = 2500L))
    }
}
