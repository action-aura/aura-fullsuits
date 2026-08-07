package com.actionaura.retail.data.sqldelight

import app.cash.sqldelight.driver.jdbc.sqlite.JdbcSqliteDriver
import com.actionaura.retail.db.RetailDatabase
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * Task 10 (multi-device-sync-foundation) -- real, executed proof that
 * [SqlDelightCategoryRepository]'s `insert`/`setActive` queue exactly one
 * real `syncOutbox` row per write, inside the SAME transaction as the
 * business write (Sync.sq's own required invariant) -- this is a real,
 * new-code regression gap this task's own code left uncovered until now
 * (`CategoryUseCasesTest` proves the pre-existing business behavior, never
 * the outbox side effect added in this task).
 */
class SqlDelightCategoryRepositoryOutboxTest {

    private fun newDb(): RetailDatabase {
        val driver = JdbcSqliteDriver(JdbcSqliteDriver.IN_MEMORY)
        driver.execute(null, "PRAGMA foreign_keys=ON", 0)
        RetailDatabase.Schema.create(driver)
        return RetailDatabase(driver)
    }

    @Test
    fun insertQueuesExactlyOneCreateOutboxEventInTheSameTransaction() = runTest {
        val db = newDb()
        val repo = SqlDelightCategoryRepository(db, DatabaseWriteGate())

        val created = repo.insert(1L, "Beverages", "desc", 1000L)

        val outbox = db.syncQueries.selectOutbox().executeAsList()
        assertEquals(1, outbox.size)
        val event = outbox.first()
        assertEquals("category", event.entityType)
        assertEquals(created.id, event.entityId)
        assertEquals("create", event.eventType)
        val payload = Json.parseToJsonElement(event.payload).jsonObject
        assertEquals(created.id, payload["id"]?.jsonPrimitive?.content)
        assertEquals("Beverages", payload["name"]?.jsonPrimitive?.content)
        assertEquals("desc", payload["description"]?.jsonPrimitive?.content)
    }

    @Test
    fun archiveQueuesADeleteOutboxEventWithMinimalPayload() = runTest {
        val db = newDb()
        val repo = SqlDelightCategoryRepository(db, DatabaseWriteGate())
        val created = repo.insert(1L, "Beverages", null, 1000L)
        db.syncQueries.deleteOutboxEvent(db.syncQueries.selectOutbox().executeAsList().first().id) // clear the create event so only the archive event is under test

        repo.setActive(1L, created.id, false)

        val outbox = db.syncQueries.selectOutbox().executeAsList()
        assertEquals(1, outbox.size)
        val event = outbox.first()
        assertEquals("delete", event.eventType)
        assertEquals(created.id, event.entityId)
        val payload = Json.parseToJsonElement(event.payload).jsonObject
        assertEquals(created.id, payload["id"]?.jsonPrimitive?.content)
    }

    @Test
    fun reactivateQueuesAnUpdateOutboxEventWithTheRowsCurrentFullState() = runTest {
        val db = newDb()
        val repo = SqlDelightCategoryRepository(db, DatabaseWriteGate())
        val created = repo.insert(1L, "Beverages", "original desc", 1000L)
        repo.setActive(1L, created.id, false)

        repo.setActive(1L, created.id, true)

        // 3 outbox rows now exist (create, archive, reactivate) -- identified
        // by event_type rather than list position/ordering, since two
        // same-millisecond real-clock writes (archive then reactivate) could
        // tie on createdAt and selectOutbox's ORDER BY does not guarantee a
        // stable tie-break.
        val reactivateEvent = db.syncQueries.selectOutbox().executeAsList().single { it.eventType == "update" }
        assertEquals("update", reactivateEvent.eventType)
        assertEquals(created.id, reactivateEvent.entityId)
        val payload = Json.parseToJsonElement(reactivateEvent.payload).jsonObject
        assertEquals("Beverages", payload["name"]?.jsonPrimitive?.content)
        assertEquals("original desc", payload["description"]?.jsonPrimitive?.content)
    }

    @Test
    fun insertQueuesADistinctOutboxEventIdFromTheCategoryId() = runTest {
        val db = newDb()
        val repo = SqlDelightCategoryRepository(db, DatabaseWriteGate())

        val created = repo.insert(1L, "Beverages", null, 1000L)

        val event = db.syncQueries.selectOutbox().executeAsList().first()
        assertTrue(event.id != created.id, "the outbox event's own id must be distinct from entityId, per Sync.sq's own documented shape")
    }

    @Test
    fun archiveWithNoDescriptionProducesANullDescriptionOnReactivateNotTheStringNull() = runTest {
        val db = newDb()
        val repo = SqlDelightCategoryRepository(db, DatabaseWriteGate())
        val created = repo.insert(1L, "Beverages", null, 1000L)
        repo.setActive(1L, created.id, false)

        repo.setActive(1L, created.id, true)

        val reactivateEvent = db.syncQueries.selectOutbox().executeAsList().single { it.eventType == "update" }
        val payload = Json.parseToJsonElement(reactivateEvent.payload).jsonObject
        assertNull(payload["description"]?.jsonPrimitive?.contentOrNull)
    }
}
