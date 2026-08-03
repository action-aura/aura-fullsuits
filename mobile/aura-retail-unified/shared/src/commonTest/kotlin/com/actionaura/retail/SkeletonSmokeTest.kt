package com.actionaura.retail

import com.actionaura.retail.platform.LogLevel
import com.actionaura.retail.platform.LoggingSink
import kotlin.test.Test
import kotlin.test.assertEquals

/**
 * Milestone 2 smoke test -- proves commonTest actually runs against
 * commonMain code on every configured target. Not a real feature test;
 * Milestone 3 (financial core + differential tests) is where the real
 * commonTest suite begins.
 */
class SkeletonSmokeTest {
    private class RecordingLoggingSink : LoggingSink {
        val entries = mutableListOf<Pair<LogLevel, String>>()
        override fun log(level: LogLevel, tag: String, message: String) {
            entries.add(level to message)
        }
    }

    @Test
    fun platformContractCanBeImplementedAndInvokedFromCommonTest() {
        val sink = RecordingLoggingSink()
        sink.log(LogLevel.INFO, "test", "hello")
        assertEquals(1, sink.entries.size)
        assertEquals(LogLevel.INFO to "hello", sink.entries.first())
    }
}
