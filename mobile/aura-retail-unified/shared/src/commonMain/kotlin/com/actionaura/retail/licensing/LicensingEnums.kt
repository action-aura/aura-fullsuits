package com.actionaura.retail.licensing

import kotlinx.serialization.Serializable

/**
 * M7.17 -- real, audited enums only (`platform-authority-audit.md`,
 * `license-state-machine-contract.md`, `installation-authority-contract.md`).
 * No invented value is added to any of these -- each member is a real
 * value found in Owner's own code/schema, cited in the linked doc.
 */

/** Real seeded `owner_platforms` rows today (`app/catalog/services.py:32`). No IOS row exists yet. */
@Serializable
enum class LicensingPlatform { WINDOWS, ANDROID }

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
