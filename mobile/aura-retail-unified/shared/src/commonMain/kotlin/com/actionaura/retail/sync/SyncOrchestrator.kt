package com.actionaura.retail.sync

import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.db.SyncOutbox
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.withLock
import kotlinx.datetime.Instant
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.longOrNull

/**
 * Task 10 (multi-device-sync-foundation) -- the real push/pull
 * orchestration loop this module previously had zero of. Drains
 * `syncOutbox` to Owner's relay ([SyncTransport.push]), applies incoming
 * remote events ([SyncTransport.pull]), and exposes [nudge] so a local
 * write can trigger an immediate push instead of waiting out the full
 * poll interval -- the real, coroutine-based sibling of desktop's
 * `SyncService` (`commercial_runtime/sync/sync_service.py`, Task 5) and
 * Ktor client this module's own JVM/coroutines idioms, not a port.
 *
 * The task brief's own draft code is illustrative only (its own brief
 * says so explicitly) and does not match this module's real APIs in two
 * load-bearing ways this class deliberately corrects:
 *
 * 1. **[SyncTransport.push]/[SyncTransport.pull] return
 *    [SyncTransportOutcome], they never throw for a network/offline/
 *    rejection failure** (confirmed by reading `SyncTransport.kt`
 *    directly) -- so there is no "offline" exception to catch in the
 *    first place; a non-[SyncTransportOutcome.Success] result is simply
 *    left alone (outbox row kept, cursor not advanced) for the next tick.
 *    The ONLY thing that can throw out of a push/pull call is
 *    [DeviceSigner.sign]/[DeviceSigner.publicKeyBytes] failing (Task 7's
 *    real `IllegalStateException` on a corrupt/invalidated persisted
 *    signing key -- `PlatformDeviceSigner`'s own KDoc: "this must be
 *    surfaced/retried by the caller, never silently papered over") --
 *    signing happens BEFORE `SyncTransport`'s own internal `try`, so that
 *    exception genuinely propagates out of `push()`/`pull()` uncaught.
 *    This class therefore never wraps a push/pull call in a blanket
 *    `catch (e: Exception)` anywhere (neither here nor in [start]'s loop
 *    nor in [nudge]) -- doing so, as the brief's own draft does, would
 *    silently swallow exactly that signer failure forever, one tick at a
 *    time, in direct violation of the one invariant Task 7 established.
 *    A signer failure is allowed to fail the specific coroutine it
 *    occurred in loudly; callers construct [scope] with a `SupervisorJob`
 *    (`AuraAppContainer`'s own choice) so that failure never cascades to
 *    unrelated coroutines sharing the same scope.
 * 2. **The gate's [DatabaseWriteGate.mutex] is held only around the local
 *    SQLite read/write, never across the network round-trip.** The
 *    draft's `gate.mutex.withLock { ... transport.push(events) ... }`
 *    shape would hold that mutex -- the ONE mutex `DatabaseWriteGate`'s
 *    own KDoc says every repository in this whole app serializes through,
 *    for the single shared JDBC connection -- for up to
 *    [SyncRelayConfiguration.requestTimeoutMillis] (10s default) on every
 *    single poll tick, freezing every unrelated category/product/branch
 *    write app-wide for the duration. This is safe to avoid because
 *    `pushOnce`/`pullOnce` never delete/update a blanket range -- they
 *    always target the SPECIFIC ids/events read moments earlier, so a new
 *    local write landing in the gap between "read outbox" and "delete
 *    pushed rows" is simply never touched (picked up whole on the next
 *    tick) -- the exact same "only sweeps up what it actually pushed"
 *    invariant desktop's own `SyncService` test suite already proved for
 *    the identical shape (`task-5-report.md`).
 */
class SyncOrchestrator(
    /**
     * Re-resolved on every single push/pull attempt, never cached --
     * mirrors desktop's own `client_factory` rationale exactly
     * (`task-5-report.md`: "a client built once at process start would
     * freeze whatever installation_id was on disk at that moment ...
     * commonly None"). A real activation committed after this
     * orchestrator was constructed (or after the app process started) is
     * picked up on the very next tick with no restart required.
     */
    private val transportProvider: suspend () -> SyncTransport?,
    private val database: RetailDatabase,
    private val gate: DatabaseWriteGate,
    private val scope: CoroutineScope,
) {
    private var pollJob: Job? = null
    private val wireJson = Json { ignoreUnknownKeys = true }

    /**
     * Drains `syncOutbox` and pushes it as one batch. A no-op (not an
     * error) when sync isn't configured/activated yet ([transportProvider]
     * returns `null`) or the outbox is empty. Only clears the exact rows
     * that were actually read and actually acknowledged -- see this
     * class's own KDoc point 2.
     */
    suspend fun pushOnce() {
        val transport = transportProvider() ?: return
        val outbox = gate.mutex.withLock { database.syncQueries.selectOutbox().executeAsList() }
        if (outbox.isEmpty()) return

        val events = outbox.map { it.toEnvelope() }
        val outcome = transport.push(events)
        if (outcome is SyncTransportOutcome.Success) {
            gate.mutex.withLock {
                database.transaction {
                    outbox.forEach { database.syncQueries.deleteOutboxEvent(it.id) }
                }
            }
        }
        // Any other outcome (Timeout/NetworkFailure/TlsFailure/Rejected/
        // MalformedResponse) leaves every outbox row exactly as it was --
        // retried whole on the next tick/nudge, never partially cleared.
    }

    /**
     * Pulls every remote event since the locally persisted cursor and
     * applies it. A no-op when sync isn't configured/activated yet.
     * Applying the batch and advancing the cursor happen in one local
     * transaction, so a failure partway through a multi-event batch never
     * leaves the cursor advanced past an event that wasn't actually
     * applied (mirrors the atomicity desktop's own `pull_once()` test
     * suite proved for the identical shape).
     */
    suspend fun pullOnce() {
        val transport = transportProvider() ?: return
        // `selectCursor` projects the single `lastSeq` column (not `SELECT *`),
        // so SQLDelight generates this as a plain `Query<Long>` -- no `SyncCursor`
        // wrapper type/`.lastSeq` accessor exists to chain here.
        val cursor = gate.mutex.withLock { database.syncQueries.selectCursor().executeAsOne() }
        val outcome = transport.pull(cursor)
        if (outcome is SyncTransportOutcome.Success) {
            gate.mutex.withLock {
                try {
                    database.transaction {
                        outcome.value.events.forEach { applyEvent(it) }
                        database.syncQueries.updateCursor(outcome.value.cursor)
                    }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    // Real, narrow, known limitation (not silently
                    // pretending this can't happen): a genuine cross-device
                    // conflict -- e.g. two devices independently creating a
                    // category with the same (company_id, name) while
                    // offline -- can violate `categories_company_name`'s
                    // partial unique index when applying a remote event.
                    // Deliberately scoped to ONLY this local DB-apply
                    // block, never around `transport.pull()`/`.push()`
                    // themselves, so a signer failure (see class KDoc
                    // point 1) can never be caught here -- this catch only
                    // ever sees real SQLDelight/JDBC exceptions from the
                    // local write. The cursor is correctly left
                    // un-advanced (the whole `database.transaction` block
                    // rolled back), so the same batch is retried next
                    // tick -- which will fail identically until the
                    // conflict is resolved. Real conflict resolution is
                    // out of this task's scope (not asked for in the
                    // brief); flagged in the task report as a known gap.
                }
            }
        }
    }

    private fun applyEvent(ev: PulledSyncEvent) {
        if (ev.entityType != "category") return // only categories are sync-instrumented as of this task
        val payload = ev.payload
        when (ev.eventType) {
            "create", "update" -> {
                val id = payload["id"]?.let { it as? JsonPrimitive }?.contentOrNull ?: ev.entityId
                val companyId = payload["company_id"]?.let { it as? JsonPrimitive }?.longOrNull ?: 1L
                val name = payload["name"]?.let { it as? JsonPrimitive }?.contentOrNull
                if (name == null) return // malformed remote payload -- skip this one event, never crash the whole batch
                val description = payload["description"]?.let { it as? JsonPrimitive }?.contentOrNull
                // Portable check-then-branch, not a real SQLite UPSERT --
                // see `updateCategoryFields`'s own KDoc in Catalog.sq for
                // why (minSdk 26's bundled SQLite predates SQLite UPSERT).
                val exists = database.catalogQueries.selectCategoryById(id, companyId).executeAsOneOrNull() != null
                if (exists) {
                    database.catalogQueries.updateCategoryFields(name, description, id, companyId)
                } else {
                    database.catalogQueries.importCategory(id, companyId, name, description, ev.localCreatedAtMillis())
                }
            }
            "delete" -> {
                val companyId = payload["company_id"]?.let { it as? JsonPrimitive }?.longOrNull ?: 1L
                database.catalogQueries.updateCategoryStatus("inactive", ev.entityId, companyId)
            }
            else -> Unit // unknown event type -- silently skipped, cursor still advances (matches desktop's own applyEvent convention)
        }
    }

    /** Starts the background poll loop; idempotent (a second call while already running is a no-op). */
    fun start(pollIntervalMs: Long = 10_000) {
        if (pollJob?.isActive == true) return
        pollJob = scope.launch {
            while (isActive) {
                pushOnce()
                pullOnce()
                delay(pollIntervalMs)
            }
        }
    }

    fun stop() {
        pollJob?.cancel()
        pollJob = null
    }

    /**
     * Call this right after any local category write commits -- makes
     * "push the instant connectivity/activation returns" real, not just
     * eventual within the poll interval. Fire-and-forget on [scope],
     * deliberately never wrapped in a `try/catch` here either (see class
     * KDoc point 1) -- a caller that wants nudge failures observed can
     * inspect [scope]'s own exception handling.
     */
    fun nudge() {
        scope.launch { pushOnce() }
    }

    private fun SyncOutbox.toEnvelope(): SyncEventEnvelope = SyncEventEnvelope(
        id = id,
        entityType = entityType,
        entityId = entityId,
        eventType = eventType,
        payload = wireJson.parseToJsonElement(payload).jsonObject,
        createdAt = Instant.fromEpochMilliseconds(createdAt).toString(),
    )

    private fun PulledSyncEvent.localCreatedAtMillis(): Long =
        runCatching { Instant.parse(createdAt).toEpochMilliseconds() }.getOrElse { kotlinx.datetime.Clock.System.now().toEpochMilliseconds() }
}
