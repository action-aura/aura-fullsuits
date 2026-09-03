package com.actionaura.retail.sync

/**
 * One of the two independent sync streams this device drives every tick.
 * `commercial_runtime/sync/` moves retail's business data (products, sales,
 * customers, ...) through `retail.db`'s outbox/cursor; the newer registry
 * stream moves `user`/`user_permission` rows out of `registry.db` -- the
 * whole reason a cashier created on desktop could not log in on Android
 * until this stream existed. The two databases have no foreign keys between
 * them, so neither stream's rows can ever be blocked on the other's.
 *
 * [prefix] is the local route prefix this device's own embedded backend
 * (Chaquopy Flask, reached via [com.actionaura.retail.server.ServerBootstrap])
 * registers each stream's four `_internal/...` routes under -- see
 * [SyncCoordinator]'s class doc for what those four routes are. Owner's
 * relay itself is NOT duplicated per stream: `/api/sync/v1/push|pull`
 * ingests events generically regardless of which local stream produced
 * them (every event already carries its own `entity_type`), so only the
 * LOCAL half of the wiring needs a stream-specific path.
 */
internal data class SyncStream(val label: String, val prefix: String) {
    companion object {
        val RETAIL = SyncStream(label = "retail", prefix = "/api/sync")
        val REGISTRY = SyncStream(label = "registry", prefix = "/api/registry-sync")

        /** Both streams, in this fixed order. The order is NOT
         *  correctness-critical -- see the class doc above on why neither
         *  stream's push/pull can ever be blocked on the other's rows -- it
         *  is a latency preference only: retail is the higher-volume, more
         *  latency-sensitive stream (a cashier mid-sale), so it goes
         *  first. */
        val ALL: List<SyncStream> = listOf(RETAIL, REGISTRY)
    }
}
