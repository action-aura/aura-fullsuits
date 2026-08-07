package com.actionaura.retail.sync

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.data.sqldelight.DatabaseWriteGate
import com.actionaura.retail.db.RetailDatabase
import com.actionaura.retail.licensing.transport.LicensingEnvironment
import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * Task 10 (multi-device-sync-foundation) -- real, executed regression
 * coverage for [SyncOrchestrator], following the established convention
 * `SyncTransportMockTest`/`HttpExternalLicensingTransportMockTest` set
 * (Ktor's real [MockEngine], never a hand-rolled fake HTTP stack), driven
 * against a real in-memory SQLDelight [RetailDatabase] (matching
 * `CategoryUseCasesTest`'s own convention) -- not a fake repository.
 */
class SyncOrchestratorTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    private fun configuration() = SyncRelayConfiguration(environment = LicensingEnvironment.DEVELOPMENT, baseUrl = "http://127.0.0.1:5551")

    private fun transportWith(engine: MockEngine, signer: DeviceSigner = FakeDeviceSigner()): SyncTransport =
        SyncTransport(HttpClient(engine), configuration(), signer, installationId = "mock-installation-id")

    // ---- pushOnce ----

    @Test
    fun pushOnceIsANoOpWhenTransportProviderResolvesNull() = runTest {
        val db = newDb()
        val orchestrator = SyncOrchestrator({ null }, db, DatabaseWriteGate(), this)
        db.syncQueries.insertOutboxEvent("e1", "category", "c1", "create", "{}", 1000L)

        orchestrator.pushOnce() // must not throw, must not attempt a connection

        assertEquals(1, db.syncQueries.selectOutbox().executeAsList().size, "no transport configured -- outbox must be left untouched")
    }

    @Test
    fun pushOnceIsANoOpWhenOutboxIsEmpty() = runTest {
        val db = newDb()
        var engineInvoked = false
        val engine = MockEngine {
            engineInvoked = true
            respond("""{"stored":0,"received":0}""", HttpStatusCode.OK, headersOf(HttpHeaders.ContentType, "application/json"))
        }
        val orchestrator = SyncOrchestrator({ transportWith(engine) }, db, DatabaseWriteGate(), this)

        orchestrator.pushOnce()

        assertTrue(!engineInvoked, "an empty outbox must never even attempt a network call")
    }

    @Test
    fun pushOnceClearsExactlyThePushedRowsOnSuccess() = runTest {
        val db = newDb()
        db.syncQueries.insertOutboxEvent("e1", "category", "c1", "create", """{"id":"c1","name":"Beverages"}""", 1000L)
        db.syncQueries.insertOutboxEvent("e2", "category", "c2", "create", """{"id":"c2","name":"Snacks"}""", 2000L)
        val engine = MockEngine { respond("""{"stored":2,"received":2}""", HttpStatusCode.OK, headersOf(HttpHeaders.ContentType, "application/json")) }
        val orchestrator = SyncOrchestrator({ transportWith(engine) }, db, DatabaseWriteGate(), this)

        orchestrator.pushOnce()

        assertTrue(db.syncQueries.selectOutbox().executeAsList().isEmpty())
    }

    @Test
    fun pushOnceLeavesTheOutboxUntouchedOnARealServerRejection() = runTest {
        val db = newDb()
        db.syncQueries.insertOutboxEvent("e1", "category", "c1", "create", """{"id":"c1","name":"Beverages"}""", 1000L)
        val engine = MockEngine { respond("""{"reason_code":"INVALID_EVENT"}""", HttpStatusCode.BadRequest, headersOf(HttpHeaders.ContentType, "application/json")) }
        val orchestrator = SyncOrchestrator({ transportWith(engine) }, db, DatabaseWriteGate(), this)

        orchestrator.pushOnce()

        assertEquals(1, db.syncQueries.selectOutbox().executeAsList().size, "a rejected/failed push must leave every outbox row exactly as it was, for the next tick")
    }

    @Test
    fun pushOnceOnlySweepsUpTheSpecificRowsItActuallyPushedNeverARowThatArrivesDuringTheNetworkCall() = runTest {
        val db = newDb()
        db.syncQueries.insertOutboxEvent("e1", "category", "c1", "create", """{"id":"c1","name":"Beverages"}""", 1000L)
        // Simulates a real local write landing concurrently, between this
        // pushOnce() reading the outbox and the mock relay's response
        // arriving -- the same real hazard desktop's own test suite proved
        // safe for the identical "only deletes what it actually pushed"
        // shape (task-5-report.md).
        val engine = MockEngine {
            db.syncQueries.insertOutboxEvent("e2", "category", "c2", "create", """{"id":"c2","name":"Snacks"}""", 2000L)
            respond("""{"stored":1,"received":1}""", HttpStatusCode.OK, headersOf(HttpHeaders.ContentType, "application/json"))
        }
        val orchestrator = SyncOrchestrator({ transportWith(engine) }, db, DatabaseWriteGate(), this)

        orchestrator.pushOnce()

        val remaining = db.syncQueries.selectOutbox().executeAsList()
        assertEquals(1, remaining.size)
        assertEquals("e2", remaining.first().id, "the concurrently-landed row must survive; only e1 (actually pushed) may be cleared")
    }

    @Test
    fun pushOnceLetsARealDeviceSignerFailurePropagateUncaughtNeverSwallowedAsOffline() = runTest {
        val db = newDb()
        db.syncQueries.insertOutboxEvent("e1", "category", "c1", "create", """{"id":"c1","name":"Beverages"}""", 1000L)
        val engine = MockEngine { error("must never reach the network -- signing must fail first") }
        val throwingSigner = object : DeviceSigner {
            override suspend fun publicKeyBytes(): ByteArray = error("device signing key unavailable")
            override suspend fun sign(message: ByteArray): ByteArray = error("device signing key unavailable")
        }
        val orchestrator = SyncOrchestrator({ transportWith(engine, throwingSigner) }, db, DatabaseWriteGate(), this)

        assertFailsWith<IllegalStateException> { orchestrator.pushOnce() }
        assertEquals(1, db.syncQueries.selectOutbox().executeAsList().size, "a signer failure must never be treated as a successful push")
    }

    // ---- pullOnce ----

    @Test
    fun pullOnceInsertsANewLocalCategoryOnARemoteCreateEvent() = runTest {
        val db = newDb()
        val engine = MockEngine {
            respond(
                """{"events":[{"id":"e1","entity_type":"category","entity_id":"remote-1","event_type":"create","payload":{"id":"remote-1","company_id":1,"name":"Beverages","description":"desc"},"created_at":"2026-08-07T00:00:00Z","seq":1}],"cursor":1}""",
                HttpStatusCode.OK, headersOf(HttpHeaders.ContentType, "application/json"),
            )
        }
        val orchestrator = SyncOrchestrator({ transportWith(engine) }, db, DatabaseWriteGate(), this)

        orchestrator.pullOnce()

        val row = db.catalogQueries.selectCategoryById("remote-1", 1L).executeAsOneOrNull()
        assertEquals("Beverages", row?.name)
        assertEquals("desc", row?.description)
        assertEquals("active", row?.status)
        assertEquals(1L, db.syncQueries.selectCursor().executeAsOne(), "cursor must advance to the server-returned value")
    }

    @Test
    fun pullOnceUpdatesAnExistingLocalCategoryWithoutClobberingItsOriginalCreatedAt() = runTest {
        val db = newDb()
        db.catalogQueries.importCategory("remote-1", 1L, "Old Name", null, 5000L)
        val engine = MockEngine {
            respond(
                """{"events":[{"id":"e1","entity_type":"category","entity_id":"remote-1","event_type":"update","payload":{"id":"remote-1","company_id":1,"name":"New Name","description":null},"created_at":"2026-08-07T00:00:00Z","seq":2}],"cursor":2}""",
                HttpStatusCode.OK, headersOf(HttpHeaders.ContentType, "application/json"),
            )
        }
        val orchestrator = SyncOrchestrator({ transportWith(engine) }, db, DatabaseWriteGate(), this)

        orchestrator.pullOnce()

        val row = db.catalogQueries.selectCategoryById("remote-1", 1L).executeAsOneOrNull()
        assertEquals("New Name", row?.name)
        assertNull(row?.description)
        assertEquals(5000L, row?.created_at, "an update must never overwrite the row's original local created_at with the remote event's own timestamp")
    }

    @Test
    fun pullOnceMarksACategoryInactiveOnARemoteDeleteEvent() = runTest {
        val db = newDb()
        db.catalogQueries.importCategory("remote-1", 1L, "Beverages", null, 1000L)
        val engine = MockEngine {
            respond(
                """{"events":[{"id":"e1","entity_type":"category","entity_id":"remote-1","event_type":"delete","payload":{"id":"remote-1","company_id":1},"created_at":"2026-08-07T00:00:00Z","seq":3}],"cursor":3}""",
                HttpStatusCode.OK, headersOf(HttpHeaders.ContentType, "application/json"),
            )
        }
        val orchestrator = SyncOrchestrator({ transportWith(engine) }, db, DatabaseWriteGate(), this)

        orchestrator.pullOnce()

        assertEquals("inactive", db.catalogQueries.selectCategoryById("remote-1", 1L).executeAsOneOrNull()?.status)
    }

    @Test
    fun pullOnceLeavesTheCursorUntouchedOnFailure() = runTest {
        val db = newDb()
        val engine = MockEngine { respond("""{"reason_code":"INVALID_SINCE"}""", HttpStatusCode.BadRequest, headersOf(HttpHeaders.ContentType, "application/json")) }
        val orchestrator = SyncOrchestrator({ transportWith(engine) }, db, DatabaseWriteGate(), this)

        orchestrator.pullOnce()

        assertEquals(0L, db.syncQueries.selectCursor().executeAsOne())
    }

    // ---- nudge / start / stop ----
    //
    // These lifecycle tests deliberately never construct a real
    // MockEngine-backed SyncTransport: Ktor's MockEngine defaults to
    // `Dispatchers.Default` internally (a real, non-virtual dispatcher),
    // which `runTest`'s `advanceTimeBy`/`advanceUntilIdle` (virtual-clock
    // control over the TestScheduler only) cannot deterministically wait
    // on -- confirmed the hard way: an earlier version of these three
    // tests, built against a real MockEngine transport, was genuinely
    // flaky/wrong (a `start()` test observed only 1 tick after
    // `advanceTimeBy(3500)` at a 1s interval). A `transportProvider` that
    // always resolves `null` (never actually reaches a transport/engine)
    // keeps every suspension point confined to the test dispatcher, which
    // is exactly what a deterministic loop/idempotency/nudge-timing proof
    // needs -- `pushOnce`/`pullOnce`'s actual push/pull *semantics* already
    // have real MockEngine-backed coverage above; this section is purely
    // about the scheduling/lifecycle contract around them.

    @Test
    fun nudgeReturnsImmediatelyAndTheActualPushRunsAsynchronously() = runTest {
        val db = newDb()
        var providerCalls = 0
        val orchestrator = SyncOrchestrator({ providerCalls++; null }, db, DatabaseWriteGate(), this)

        orchestrator.nudge() // fire-and-forget -- must return before the launched coroutine runs
        assertEquals(0, providerCalls, "nudge() must not have synchronously resolved/pushed yet")

        advanceUntilIdle() // let the launched coroutine actually run
        assertEquals(1, providerCalls, "nudge()'s background push must have run exactly once by the time the scope goes idle")
    }

    @Test
    fun startPollsRepeatedlyAndStopStopsIt() = runTest {
        val db = newDb()
        var providerCalls = 0
        val orchestrator = SyncOrchestrator({ providerCalls++; null }, db, DatabaseWriteGate(), this)

        // Deliberately `advanceTimeBy` only, never `advanceUntilIdle()`,
        // while the loop is still running: `start()`'s `while (isActive)`
        // loop keeps re-scheduling itself via `delay()` forever until
        // `stop()` is called, so `advanceUntilIdle()` against a still-active
        // infinite loop finds new virtual-time work on every pass and hangs
        // (real, observed failure: an earlier version of this test using
        // `advanceUntilIdle()` here genuinely hung for runTest's full 1-real-
        // minute watchdog before failing). `advanceTimeBy` is bounded --
        // exactly the right tool for "let N ticks happen, no more."
        orchestrator.start(pollIntervalMs = 1_000)
        advanceTimeBy(3_500) // ~3 ticks
        // 2 provider calls per tick (pushOnce + pullOnce): tick at t=0 (before
        // the first delay), t=1000, t=2000, t=3000 -- 4 ticks x 2 = 8.
        val callsBeforeStop = providerCalls
        assertTrue(callsBeforeStop >= 6, "expected at least 3 poll ticks (>= 6 provider calls) in 3.5s at a 1s interval, got $callsBeforeStop")

        orchestrator.stop() // cancels the loop -- now safe to drain to idle
        advanceUntilIdle()
        assertEquals(callsBeforeStop, providerCalls, "no further ticks must occur after stop()")
    }

    @Test
    fun startIsIdempotentASecondCallWhileAlreadyRunningDoesNotDoubleTheLoop() = runTest {
        val db = newDb()
        var providerCalls = 0
        val orchestrator = SyncOrchestrator({ providerCalls++; null }, db, DatabaseWriteGate(), this)

        orchestrator.start(pollIntervalMs = 1_000)
        orchestrator.start(pollIntervalMs = 1_000) // second call while already running -- must be a no-op, not a second concurrent loop
        advanceTimeBy(1_500) // bounded, not advanceUntilIdle() -- see startPollsRepeatedlyAndStopStopsIt's own comment on why
        val afterOneTick = providerCalls

        orchestrator.stop()
        advanceUntilIdle()
        // One loop ticking at t=0 and t=1000 (2 ticks within a 1.5s window
        // at a 1s interval) x 2 provider calls per tick (pushOnce + pullOnce)
        // = exactly 4. A double-registered second concurrent loop would show
        // double that (8) -- asserting the exact expected count, not a loose
        // range, since virtual time makes this fully deterministic.
        assertEquals(4, afterOneTick, "expected exactly one loop's worth of ticks (4 provider calls) -- start() may have started a second concurrent loop")
    }

    private class FakeDeviceSigner : DeviceSigner {
        override suspend fun publicKeyBytes(): ByteArray = ByteArray(32) { it.toByte() }
        override suspend fun sign(message: ByteArray): ByteArray = ByteArray(64) { 0x24 }
    }
}
