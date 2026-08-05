package com.actionaura.retail.licensing.transport

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * M9.11 -- mobile-side idempotency coordinator
 * (`mobile-activation-idempotency.md`). The server remains the final
 * idempotency authority (`activation-idempotency-contract.md`, M7.10)
 * -- this coordinator only guarantees the client-side half: one
 * stable key per logical attempt, duplicate taps reused, a materially
 * changed request gets a new key.
 */
class ActivationIdempotencyCoordinator(private val keyGenerator: () -> String) {
    private val mutex = Mutex()
    private var currentKey: String? = null
    private var currentFingerprint: Int? = null
    private var inFlight: Boolean = false

    /** Returns the stable key for this logical attempt -- reuses the in-flight key for an identical fingerprint, mints a fresh one otherwise. */
    suspend fun keyFor(requestFingerprint: Int): String = mutex.withLock {
        val existing = currentKey
        if (existing != null && currentFingerprint == requestFingerprint) {
            existing
        } else {
            val fresh = keyGenerator()
            currentKey = fresh
            currentFingerprint = requestFingerprint
            fresh
        }
    }

    /** Real duplicate-tap guard -- returns false (and does nothing) if an attempt is already in flight for the current key. */
    suspend fun tryBeginAttempt(): Boolean = mutex.withLock {
        if (inFlight) false else { inFlight = true; true }
    }

    suspend fun completeAttempt() = mutex.withLock { inFlight = false }

    /** A materially different request must never reuse the prior key. */
    suspend fun reset() = mutex.withLock {
        currentKey = null
        currentFingerprint = null
        inFlight = false
    }

    suspend fun isInFlight(): Boolean = mutex.withLock { inFlight }
}
