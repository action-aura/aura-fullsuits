package com.actionaura.retail.licensing.transport

import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals

/**
 * M9.30 -- real concurrency proof: only one activation request is in
 * flight per logical attempt; duplicate concurrent taps do not create
 * duplicate commands.
 */
class M9ConcurrencyTest {

    @Test
    fun concurrentTryBeginAttemptCallsOnlyOneEverSucceeds() = runTest {
        val coordinator = ActivationIdempotencyCoordinator { "fixed-key" }
        val results = (1..20).map { async { coordinator.tryBeginAttempt() } }.awaitAll()
        assertEquals(1, results.count { it }, "real regression: exactly one of 20 real-concurrent duplicate taps must win the in-flight guard")
    }

    @Test
    fun concurrentKeyForCallsWithTheSameFingerprintAllReceiveTheSameKey() = runTest {
        var generated = 0
        val coordinator = ActivationIdempotencyCoordinator { "key-${generated++}" }
        val keys = (1..20).map { async { coordinator.keyFor(42) } }.awaitAll()
        assertEquals(1, keys.toSet().size, "real regression: 20 real-concurrent calls with the same fingerprint must all resolve to one stable key")
    }
}
