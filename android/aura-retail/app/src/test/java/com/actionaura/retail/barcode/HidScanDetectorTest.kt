package com.actionaura.retail.barcode

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class HidScanDetectorTest {

    @Test
    fun fast_burst_terminated_by_enter_is_recognized_as_a_scan() {
        val d = HidScanDetector(timeoutMs = 50, minLength = 3)
        var t = 1000L
        for (ch in "8901234567") { d.onChar(ch, t); t += 5 } // 5ms between chars -- scanner speed
        assertEquals("8901234567", d.onEnter(t))
    }

    @Test
    fun slow_human_typing_terminated_by_enter_is_not_a_scan() {
        val d = HidScanDetector(timeoutMs = 50, minLength = 3)
        var t = 1000L
        for (ch in "hello") { d.onChar(ch, t); t += 200 } // 200ms between chars -- human speed
        assertNull(d.onEnter(t))
    }

    @Test
    fun a_pause_mid_sequence_resets_the_buffer_so_typing_then_a_scan_is_not_conflated() {
        val d = HidScanDetector(timeoutMs = 50, minLength = 3)
        var t = 1000L
        for (ch in "abc") { d.onChar(ch, t); t += 200 } // slow typing
        t += 500 // long pause
        for (ch in "999000111") { d.onChar(ch, t); t += 5 } // then a real fast scan
        assertEquals("999000111", d.onEnter(t))
    }

    @Test
    fun below_min_length_is_rejected_even_if_fast() {
        val d = HidScanDetector(timeoutMs = 50, minLength = 3)
        var t = 1000L
        for (ch in "ab") { d.onChar(ch, t); t += 5 }
        assertNull(d.onEnter(t))
    }

    @Test
    fun enter_with_no_preceding_characters_is_not_a_scan() {
        val d = HidScanDetector(timeoutMs = 50, minLength = 3)
        assertNull(d.onEnter(1000L))
    }

    @Test
    fun detector_is_reusable_across_multiple_scans() {
        val d = HidScanDetector(timeoutMs = 50, minLength = 3)
        var t = 1000L
        for (ch in "111222333") { d.onChar(ch, t); t += 5 }
        assertEquals("111222333", d.onEnter(t))

        t += 1000
        for (ch in "444555666") { d.onChar(ch, t); t += 5 }
        assertEquals("444555666", d.onEnter(t))
    }

    @Test
    fun reset_clears_in_progress_buffer() {
        val d = HidScanDetector(timeoutMs = 50, minLength = 3)
        var t = 1000L
        for (ch in "123") { d.onChar(ch, t); t += 5 }
        d.reset()
        // Only "45" follows reset -- below minLength, and firstCharAtMs was cleared.
        for (ch in "45") { d.onChar(ch, t); t += 5 }
        assertNull(d.onEnter(t))
    }
}
