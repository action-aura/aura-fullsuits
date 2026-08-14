package com.actionaura.retail.barcode

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Regression test for the PosScreen "replays a stale scan on (re-)entering the
 * screen" bug: HidScanBus.lastScan is a process-wide singleton with no "only
 * while POS is on screen" gate, so a LaunchedEffect keyed directly off it fires
 * immediately with whatever scan is already sitting there the moment the screen
 * (re-)enters composition. [isUnconsumedScan] is the guard PosScreen now calls
 * (extracted, not reimplemented) before treating a scan as fresh. */
class HidScanBusTest {

    @Test
    fun a_scan_with_a_seq_higher_than_what_was_already_consumed_is_unconsumed() {
        val event = ScanEvent(code = "0123456789012", seq = 5L)
        assertTrue(isUnconsumedScan(event, lastConsumedSeq = 4L))
    }

    @Test
    fun a_scan_whose_seq_was_already_consumed_must_not_be_replayed() {
        // Simulates PosScreen (re-)entering composition while HidScanBus.lastScan
        // still holds a scan consumed earlier in the session (e.g. before a tab
        // switch) -- it must not be treated as a fresh scan.
        val event = ScanEvent(code = "0123456789012", seq = 5L)
        assertFalse(isUnconsumedScan(event, lastConsumedSeq = 5L))
    }

    @Test
    fun a_scan_older_than_what_was_already_consumed_must_not_be_replayed() {
        val event = ScanEvent(code = "0123456789012", seq = 3L)
        assertFalse(isUnconsumedScan(event, lastConsumedSeq = 5L))
    }

    @Test
    fun the_very_first_scan_ever_seen_is_unconsumed() {
        // PosScreen initializes lastConsumedSeq to -1 when HidScanBus.lastScan
        // has never fired yet (fresh app process, no scan before this screen).
        val event = ScanEvent(code = "0123456789012", seq = 1L)
        assertTrue(isUnconsumedScan(event, lastConsumedSeq = -1L))
    }
}
