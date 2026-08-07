package com.actionaura.retail.sync

import com.actionaura.retail.licensing.transport.LicensingEnvironment

/**
 * Task 9 (multi-device-sync-foundation) -- versioned sync relay
 * configuration contract, mirroring
 * [com.actionaura.retail.licensing.transport.ExternalApiConfiguration]'s own
 * [validate] discipline (no production domain/IP hard-coded anywhere in
 * `commonMain`; the real, enumerated unsafe shapes are rejected here too).
 * A separate type from `ExternalApiConfiguration` rather than a reuse of
 * it: that type's `productCode`/`platform`/`contractVersion` fields are
 * licensing-specific concepts the sync relay's own wire contract
 * (`owner/app/sync/routes.py`) has no equivalent of.
 *
 * [baseUrl] is Owner's own root origin (e.g. `http://127.0.0.1:5551`, matching
 * desktop's `AURA_SYNC_RELAY_URL`, task-5-report.md) -- [SyncTransport]
 * itself appends the real, fixed `/api/sync/v1/push`/`/api/sync/v1/pull`
 * paths, never a path baked into this config value.
 */
data class SyncRelayConfiguration(
    val environment: LicensingEnvironment,
    val baseUrl: String,
    val requestTimeoutMillis: Long = 10_000,
    val connectTimeoutMillis: Long = 10_000,
    val responseTimeoutMillis: Long = 10_000,
) {
    fun validate(): SyncRelayConfigurationValidationResult {
        val problems = mutableListOf<String>()

        val schemeMatch = Regex("^([a-zA-Z][a-zA-Z0-9+.-]*)://").find(baseUrl)
        val scheme = schemeMatch?.groupValues?.get(1)?.lowercase()
        when {
            scheme == null -> problems += "baseUrl has no recognizable scheme"
            scheme == "http" && environment == LicensingEnvironment.PRODUCTION -> problems += "cleartext HTTP is never permitted in production configuration"
            scheme != "http" && scheme != "https" -> problems += "unsupported scheme '$scheme'"
        }
        if (baseUrl.contains("@")) problems += "baseUrl must not embed credentials"
        if (baseUrl.contains("#")) problems += "baseUrl must not embed a URL fragment"
        if (baseUrl.contains("?")) problems += "baseUrl must not embed a query string"
        if (scheme == "http" && environment != LicensingEnvironment.PRODUCTION && !isExplicitLocalDevelopmentHost(baseUrl)) {
            problems += "cleartext HTTP outside production requires an explicit local-development host"
        }
        if (requestTimeoutMillis <= 0 || connectTimeoutMillis <= 0 || responseTimeoutMillis <= 0) problems += "timeouts must be positive"

        return if (problems.isEmpty()) SyncRelayConfigurationValidationResult.Valid else SyncRelayConfigurationValidationResult.Invalid(problems)
    }

    private fun isExplicitLocalDevelopmentHost(url: String): Boolean =
        Regex("^https?://(localhost|127\\.0\\.0\\.1|10\\.0\\.2\\.2)([:/].*)?$").matches(url)
}

sealed interface SyncRelayConfigurationValidationResult {
    data object Valid : SyncRelayConfigurationValidationResult
    data class Invalid(val problems: List<String>) : SyncRelayConfigurationValidationResult
}
