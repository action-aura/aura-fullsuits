package com.actionaura.retail.sync

import kotlinx.datetime.Instant

sealed interface SyncHalfHealth {
    val lastSuccessAt: Instant?

    data class Healthy(override val lastSuccessAt: Instant?) : SyncHalfHealth

    data class Degraded(
        override val lastSuccessAt: Instant?,
        val reason: String,
        /** Start of the CURRENT failure streak, not the latest failure. */
        val since: Instant,
        val lastFailureAt: Instant,
        val consecutiveFailures: Int,
    ) : SyncHalfHealth
}

data class SyncHealthSnapshot(
    val push: SyncHalfHealth = SyncHalfHealth.Healthy(null),
    val pull: SyncHalfHealth = SyncHalfHealth.Healthy(null),
) {
    val isHealthy: Boolean get() = push is SyncHalfHealth.Healthy && pull is SyncHalfHealth.Healthy
    val consecutiveFailures: Int get() = maxOf(push.failureCount(), pull.failureCount())
}

fun SyncHalfHealth.failureCount(): Int = when (this) {
    is SyncHalfHealth.Healthy -> 0
    is SyncHalfHealth.Degraded -> consecutiveFailures
}
