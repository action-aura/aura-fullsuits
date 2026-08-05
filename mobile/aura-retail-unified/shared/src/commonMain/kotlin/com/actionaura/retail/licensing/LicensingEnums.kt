package com.actionaura.retail.licensing

import kotlinx.serialization.Serializable

/**
 * M7.17 -- real, audited enums only (`platform-authority-audit.md`,
 * `license-state-machine-contract.md`, `installation-authority-contract.md`).
 * No invented value is added to any of these -- each member is a real
 * value found in Owner's own code/schema, cited in the linked doc.
 */

/**
 * M8.0 -- `WINDOWS`/`ANDROID` are real, seeded `owner_platforms` rows
 * today (`app/catalog/services.py:32`). `IOS` is added here as a
 * client-side forward-compatible contract case only
 * (`platform-contract-reconciliation-m8.md`) -- it is NOT a real
 * `owner_platforms` row and Owner does not accept it yet
 * (`ios-platform-readiness-state.md`). Never treat this enum's mere
 * inclusion of `IOS` as proof of server acceptance.
 */
@Serializable
enum class LicensingPlatform { WINDOWS, ANDROID, IOS }

/**
 * M8.0 -- real parse result for an arbitrary platform string (server
 * response or local input). Never silently coerces an unrecognized
 * value to an existing case, never defaults to `ANDROID`, never
 * accepts `ALL`/`ANY`/`MOBILE` as a known platform -- none of those
 * exist as a real `owner_platforms` row (`platform-contract-
 * reconciliation-m8.md`).
 */
sealed interface PlatformDecodeResult {
    data class Known(val platform: LicensingPlatform) : PlatformDecodeResult
    data class UnsupportedPlatform(val raw: String) : PlatformDecodeResult

    companion object {
        fun parse(raw: String): PlatformDecodeResult {
            val match = LicensingPlatform.entries.firstOrNull { it.name == raw }
            return if (match != null) Known(match) else UnsupportedPlatform(raw)
        }
    }
}

/** Real `owner_products.product_code` values (`app/catalog/services.py:28-31`). */
@Serializable
enum class LicensingProductCode { AURA_RETAIL, AURA_CLINIC }

/** Real `License.status` values, `VALID_TRANSITIONS` (`app/licensing/services.py:15-23`). No ARCHIVED. */
@Serializable
enum class LicenseStatus { DRAFT, ISSUED, ACTIVE, SUSPENDED, EXPIRED, REVOKED, REPLACED }

/** Real `Installation.status` values, `VALID_TRANSITIONS` (`app/installations/services.py:18-25`). */
@Serializable
enum class InstallationStatus { REGISTERED, PENDING_ACTIVATION, ACTIVE, SUSPENDED, DEACTIVATED, REPLACED }

/** Real `Subscription.status` values reachable in an assertion payload's `subscription_status` field. */
@Serializable
enum class SubscriptionStatus { DRAFT, PILOT, ACTIVE, PAST_DUE, SUSPENDED, EXPIRED, CANCELLED, COMPLETED }
