package com.actionaura.retail.reporting

/**
 * M5.7.10 -- real, bounded limits for the reporting authority. Stable
 * application constants (not a UI-only restriction, not yet a
 * canonical-business-setting-backed value -- no real settings
 * persistence/authority for this exists yet outside `SettingsRepository`'s
 * plain string key/value store, and no real caller has asked for a
 * per-business-configurable override; these constants are the honest,
 * additive starting point a future milestone can promote to a real
 * setting if evidence ever justifies it).
 */
object ReportingLimits {
    /** Top Products result count is clamped into `[0, MAX_TOP_PRODUCTS_LIMIT]` -- never loaded unbounded, never a request for zero/negative results, never a crash on a negative `limit`. */
    const val MAX_TOP_PRODUCTS_LIMIT = 500L

    /**
     * `getSalesTrend` rejects (does not silently truncate) a bucket list
     * longer than this -- a real N+1 shape already measured and accepted
     * at normal scale (`reporting-query-count-report.md`: 2 real queries
     * per bucket) would become a real, unbounded cost at, e.g., a
     * multi-decade daily-bucket request. 400 comfortably covers 13 months
     * of daily buckets or many years of weekly/monthly buckets -- every
     * real M5.6/M5.7 test uses well under this.
     */
    const val MAX_TREND_BUCKET_COUNT = 400

    /** Dashboard low-stock/top-product preview counts share the same Top Products bound -- one canonical limit, not a second one invented for the Dashboard specifically. */
    const val MAX_DASHBOARD_PREVIEW_LIMIT = MAX_TOP_PRODUCTS_LIMIT
}
