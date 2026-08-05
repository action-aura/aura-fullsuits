package com.actionaura.retail.licensing.transport

import com.actionaura.retail.licensing.LicensingPlatform
import com.actionaura.retail.licensing.LicensingProductCode

/**
 * M9.5 -- versioned external API configuration contract
 * (`external-api-configuration-contract.md`). No production domain,
 * IP address, or speculative Owner route is hard-coded anywhere in
 * `commonMain` -- every value here is supplied by the caller, and
 * [validate] rejects the real, enumerated unsafe shapes.
 */
enum class LicensingEnvironment { DEVELOPMENT, STAGING, PRODUCTION }

data class ExternalApiConfiguration(
    val environment: LicensingEnvironment,
    val baseUrl: String,
    val contractVersion: String = "v1",
    val requestTimeoutMillis: Long = 10_000,
    val connectTimeoutMillis: Long = 10_000,
    val responseTimeoutMillis: Long = 10_000,
    val releaseChannel: String? = null,
    val productCode: LicensingProductCode,
    val platform: LicensingPlatform,
    val safeDiagnosticMode: Boolean = false,
) {
    fun validate(): ExternalApiConfigurationValidationResult {
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
        if (Regex("(?i)(license[_-]?key|serial|token|password)").containsMatchIn(baseUrl)) problems += "baseUrl must not embed a secret-shaped value"
        if (scheme == "http" && environment != LicensingEnvironment.PRODUCTION && !isExplicitLocalDevelopmentHost(baseUrl)) {
            problems += "cleartext HTTP outside production requires an explicit local-development host"
        }
        if (requestTimeoutMillis <= 0 || connectTimeoutMillis <= 0 || responseTimeoutMillis <= 0) problems += "timeouts must be positive"

        return if (problems.isEmpty()) ExternalApiConfigurationValidationResult.Valid else ExternalApiConfigurationValidationResult.Invalid(problems)
    }

    private fun isExplicitLocalDevelopmentHost(url: String): Boolean =
        Regex("^https?://(localhost|127\\.0\\.0\\.1|10\\.0\\.2\\.2)([:/].*)?$").matches(url)
}

sealed interface ExternalApiConfigurationValidationResult {
    data object Valid : ExternalApiConfigurationValidationResult
    data class Invalid(val problems: List<String>) : ExternalApiConfigurationValidationResult
}
