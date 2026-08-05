package com.actionaura.retail.licensing.transport

import kotlin.random.Random

/**
 * M9.15 -- one bounded retry policy (`mobile-network-retry-policy.md`),
 * built from the legacy `OwnerClient.kt`'s own real, tested behavior
 * (`mobile-licensing-network-audit.md`): retry only
 * [TransportOutcome.isSafeToRetry] outcomes, bounded exponential
 * backoff with jitter, honor a real `Retry-After` when present, never
 * retry indefinitely.
 */
data class RetryPolicy(
    val maxRetries: Int = 4,
    val baseBackoffMillis: Long = 1000,
    val maxBackoffMillis: Long = 30_000,
    val jitterFraction: Double = 0.25,
) {
    init {
        require(maxRetries in 0..10) { "maxRetries must be bounded (0..10), never unbounded" }
    }

    fun backoffFor(attempt: Int, retryAfterSeconds: Long?, random: Random = Random.Default): Long {
        if (retryAfterSeconds != null) return minOf(retryAfterSeconds * 1000, maxBackoffMillis)
        val exponential = baseBackoffMillis * (1L shl (attempt - 1).coerceAtLeast(0))
        val jitter = (exponential * jitterFraction * random.nextDouble()).toLong()
        return minOf(exponential + jitter, maxBackoffMillis)
    }
}

/**
 * Executes [block] under [policy], retrying only on
 * [TransportOutcome.isSafeToRetry] outcomes. Never retries a business
 * rejection, authentication rejection, TLS failure, malformed
 * response, unsupported-contract-version, cancellation, or
 * not-configured result -- matching `mobile-network-retry-policy.md`'s
 * own explicit "do not automatically retry" list.
 */
suspend fun <T> withRetry(
    policy: RetryPolicy,
    sleep: suspend (Long) -> Unit,
    random: Random = Random.Default,
    block: suspend (attempt: Int) -> TransportOutcome<T>,
): TransportOutcome<T> {
    var lastOutcome: TransportOutcome<T> = TransportOutcome.NetworkFailure("no attempt made")
    for (attempt in 0..policy.maxRetries) {
        if (attempt > 0) {
            val retryAfter = (lastOutcome as? TransportOutcome.RateLimited)?.retryAfterSeconds
            sleep(policy.backoffFor(attempt, retryAfter, random))
        }
        val outcome = block(attempt)
        if (!outcome.isSafeToRetry()) return outcome
        lastOutcome = outcome
    }
    return lastOutcome
}
