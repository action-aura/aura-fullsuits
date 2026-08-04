package com.actionaura.retail.data

/**
 * M5.1 -- interface-only boundaries for repositories whose real
 * implementation belongs to a later milestone (no fabricated
 * implementation is written against these yet -- declaring the boundary
 * now lets the use-case/ViewModel layers in later milestones depend on a
 * stable contract without waiting for every backing store to exist,
 * mirroring how `PlatformContracts.kt` declared platform interfaces ahead
 * of their Milestone-2/4 implementations).
 *
 * Each interface below cites the real milestone that implements it for
 * real. None of these are called from any use case yet.
 */

/** M7 -- Owner licensing authority audit; identifies which local business this installation belongs to. */
interface BusinessRepository {
    suspend fun currentCompanyId(): Long
}

/** M8/M9 -- multi-device activation and local user/session model (distinct from Owner's own multi-tenant auth, which this mobile app does not share). */
interface UserRepository {
    suspend fun currentUserId(): String?
}

/** M8/M9 -- local session/activation-lease state. */
interface SessionRepository {
    suspend fun isSessionValid(): Boolean
}

/** Never SQLite-backed -- the cart is transient client-side state (`financial.Cart`, M3), not persisted between app launches. Declared here only so a future ViewModel can depend on one stable cart-access contract regardless of in-memory vs. platform-saved-state backing. */
interface CartRepository {
    suspend fun currentCart(): com.actionaura.retail.financial.Cart
}

/**
 * The six interfaces below are deliberately empty markers, not fake method
 * signatures -- their real shape is not yet designed (it depends on
 * decisions later milestones own: M5.6's exact net_sales/net_quantity/
 * net_revenue definitions, M5.8's ported-not-translated import pipeline
 * shape, M16's backup-format decisions, M9/M10's activation-policy
 * decisions). Inventing placeholder methods now would just be code deleted
 * and replaced later -- an empty marker is the honest boundary.
 */

/** M5.6 -- shared reporting authority (sales trend, top products, dashboard summary), built on ReportingRepository once the exact net_sales/net_quantity/net_revenue query definitions are settled. */
interface DashboardRepository

/** M5.6/M5.7 -- canonical reporting query authority (sales trend, top products), including the real EXPLAIN QUERY PLAN performance proof at 10K products / 100K sales scale. */
interface ReportingRepository

/** M5.8-M5.11 -- shared Import Center, ported architecturally (not line-by-line) from the real 1186-line Python import pipeline, with format security hardening and dry-run/commit transactional integrity. */
interface ImportRepository

/** M16 -- backup/restore, mirroring `commercial_runtime/security/migration_safety.py`'s real `.backup()`-API pattern (already the explicit design reference for M5.0's CatalogImporter). */
interface BackupRepository

/** M9/M10 -- policy-controlled multi-device licensing, secure activation storage. */
interface LicensingRepository

/** M20+ -- device/app health diagnostics surface. */
interface AppHealthRepository

/** M26 -- unified CI/release-pipeline version reporting. */
interface VersionRepository
