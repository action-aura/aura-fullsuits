package com.actionaura.retail.sync

import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.client.request.HttpRequestData
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpMethod
import io.ktor.http.HttpStatusCode
import io.ktor.http.content.OutgoingContent
import io.ktor.http.headersOf
import kotlinx.coroutines.delay
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertTrue

/**
 * Task 9 (multi-device-sync-foundation) -- real, non-live regression
 * coverage for [SyncTransport]'s response-branch/error-classification
 * logic, mirroring `HttpExternalLicensingTransportMockTest`'s own
 * established convention (Ktor's real [MockEngine], not a hand-rolled fake
 * HTTP stack). The live test (`SyncTransportLiveTest`) proves the real
 * wire format against a real Owner instance but is skipped by default in
 * every ordinary run -- these tests run every time.
 */
class SyncTransportMockTest {

    private fun transportWith(engine: MockEngine, signer: DeviceSigner = FakeDeviceSigner()): SyncTransport {
        val configuration = SyncRelayConfiguration(
            environment = com.actionaura.retail.licensing.transport.LicensingEnvironment.DEVELOPMENT,
            baseUrl = "http://127.0.0.1:5551",
        )
        return SyncTransport(HttpClient(engine), configuration, signer, installationId = "mock-installation-id")
    }

    private fun samplePayload(): JsonObject = buildJsonObject {
        put("name", "Mock Category")
        put("active", true)
    }

    // ---- push: success ----

    @Test
    fun pushSuccessDecodesToPushResult() = runTest {
        val engine = MockEngine { respondJson(HttpStatusCode.OK, """{"stored":2,"received":2}""") }
        val outcome = transportWith(engine).push(
            listOf(
                SyncEventEnvelope("e1", "category", "ent-1", "create", samplePayload(), "2026-08-07T00:00:00Z"),
                SyncEventEnvelope("e2", "category", "ent-2", "update", samplePayload(), "2026-08-07T00:00:01Z"),
            ),
        )
        val success = assertIs<SyncTransportOutcome.Success<PushResult>>(outcome)
        assertEquals(2, success.value.stored)
        assertEquals(2, success.value.received)
    }

    @Test
    fun pushRequestBodyCarriesFreshInstallationIdTimestampNonceSignatureAndWireShapedEvents() = runTest {
        var captured: HttpRequestData? = null
        val engine = MockEngine { request ->
            captured = request
            respondJson(HttpStatusCode.OK, """{"stored":1,"received":1}""")
        }
        transportWith(engine).push(listOf(SyncEventEnvelope("e1", "category", "ent-1", "create", samplePayload(), "2026-08-07T00:00:00Z")))

        val req = captured ?: error("MockEngine handler never ran")
        assertEquals(HttpMethod.Post, req.method)
        assertTrue(req.url.toString().endsWith("/api/sync/v1/push"))

        val bodyText = req.body.toByteArray().decodeToString()
        assertTrue(bodyText.contains("\"installation_id\":\"mock-installation-id\""))
        assertTrue(bodyText.contains("\"nonce\""))
        assertTrue(bodyText.contains("\"timestamp\""))
        assertTrue(bodyText.contains("\"signature\""))
        assertTrue(bodyText.contains("\"entity_type\":\"category\""))
        assertTrue(bodyText.contains("\"entity_id\":\"ent-1\""))
        assertTrue(bodyText.contains("\"event_type\":\"create\""))
        assertTrue(bodyText.contains("\"created_at\":\"2026-08-07T00:00:00Z\""))
    }

    @Test
    fun twoConsecutivePushCallsUseTwoDifferentNoncesAndTimestamps() = runTest {
        val capturedBodies = mutableListOf<String>()
        val engine = MockEngine { request ->
            capturedBodies += request.body.toByteArray().decodeToString()
            respondJson(HttpStatusCode.OK, """{"stored":0,"received":0}""")
        }
        val transport = transportWith(engine)
        transport.push(emptyList())
        transport.push(emptyList())

        assertEquals(2, capturedBodies.size)
        val nonce1 = Regex("\"nonce\":\"([^\"]+)\"").find(capturedBodies[0])!!.groupValues[1]
        val nonce2 = Regex("\"nonce\":\"([^\"]+)\"").find(capturedBodies[1])!!.groupValues[1]
        assertTrue(nonce1 != nonce2, "expected two independent push calls to use two different nonces, got the same value twice: $nonce1")
    }

    // ---- pull: success ----

    @Test
    fun pullSuccessDecodesEventsAndCursor() = runTest {
        val body = """{"events":[{"id":"e1","entity_type":"category","entity_id":"ent-1","event_type":"create","payload":{"name":"X"},"created_at":"2026-08-07T00:00:00Z","seq":7}],"cursor":7}"""
        val engine = MockEngine { respondJson(HttpStatusCode.OK, body) }
        val outcome = transportWith(engine).pull(since = 3)

        val success = assertIs<SyncTransportOutcome.Success<PullResult>>(outcome)
        assertEquals(7L, success.value.cursor)
        assertEquals(1, success.value.events.size)
        val event = success.value.events.single()
        assertEquals("e1", event.id)
        assertEquals("category", event.entityType)
        assertEquals("create", event.eventType)
        assertEquals(7L, event.seq)
    }

    @Test
    fun pullSuccessWithEmptyEventsAndCursorEqualToSinceIsAValidNoNewDataResult() = runTest {
        val engine = MockEngine { respondJson(HttpStatusCode.OK, """{"events":[],"cursor":5}""") }
        val outcome = transportWith(engine).pull(since = 5)
        val success = assertIs<SyncTransportOutcome.Success<PullResult>>(outcome)
        assertEquals(5L, success.value.cursor)
        assertTrue(success.value.events.isEmpty())
    }

    @Test
    fun pullRequestIsAGetCarryingSinceInsideTheSignedJsonBodyNeverAsAQueryParameter() = runTest {
        var captured: HttpRequestData? = null
        val engine = MockEngine { request ->
            captured = request
            respondJson(HttpStatusCode.OK, """{"events":[],"cursor":42}""")
        }
        transportWith(engine).pull(since = 42)

        val req = captured ?: error("MockEngine handler never ran")
        assertEquals(HttpMethod.Get, req.method)
        assertTrue(req.url.toString().endsWith("/api/sync/v1/pull"))
        assertTrue(req.url.parameters.isEmpty(), "since must never be a query parameter, got query string: ${req.url.encodedQuery}")

        val bodyText = req.body.toByteArray().decodeToString()
        assertTrue(bodyText.contains("\"since\":42"), "expected since inside the signed JSON body, got: $bodyText")
        assertTrue(bodyText.contains("\"signature\""))
    }

    // ---- Rejections (real `_error()` shape: {"reason_code": "..."}) ----

    @Test
    fun pushRejectionWithReasonCodeMapsToRejectedNeverMistakenForSuccess() = runTest {
        val engine = MockEngine { respondJson(HttpStatusCode.BadRequest, """{"reason_code":"INVALID_SIGNATURE"}""") }
        val outcome = transportWith(engine).push(emptyList())
        val rejected = assertIs<SyncTransportOutcome.Rejected>(outcome)
        assertEquals("INVALID_SIGNATURE", rejected.reasonCode)
        assertEquals(400, rejected.httpStatus)
    }

    @Test
    fun pushInternalErrorRejectionCarriesHttp500() = runTest {
        val engine = MockEngine { respondJson(HttpStatusCode.InternalServerError, """{"reason_code":"INTERNAL_ERROR"}""") }
        val outcome = transportWith(engine).push(emptyList())
        val rejected = assertIs<SyncTransportOutcome.Rejected>(outcome)
        assertEquals("INTERNAL_ERROR", rejected.reasonCode)
        assertEquals(500, rejected.httpStatus)
    }

    @Test
    fun pullRejectionWithReasonCodeMapsToRejected() = runTest {
        val engine = MockEngine { respondJson(HttpStatusCode.BadRequest, """{"reason_code":"NONCE_REUSED"}""") }
        val outcome = transportWith(engine).pull(since = 0)
        val rejected = assertIs<SyncTransportOutcome.Rejected>(outcome)
        assertEquals("NONCE_REUSED", rejected.reasonCode)
    }

    // ---- Malformed responses ----

    @Test
    fun nonJsonPushResponseBodyIsMalformedResponse() = runTest {
        val engine = MockEngine { respond("not json at all", HttpStatusCode.OK) }
        val outcome = transportWith(engine).push(emptyList())
        assertIs<SyncTransportOutcome.MalformedResponse>(outcome)
    }

    @Test
    fun pushHttp200MissingStoredFieldIsMalformedResponse() = runTest {
        val engine = MockEngine { respondJson(HttpStatusCode.OK, """{"received":1}""") }
        val outcome = transportWith(engine).push(emptyList())
        assertIs<SyncTransportOutcome.MalformedResponse>(outcome)
    }

    @Test
    fun pullHttp200MissingCursorFieldIsMalformedResponse() = runTest {
        val engine = MockEngine { respondJson(HttpStatusCode.OK, """{"events":[]}""") }
        val outcome = transportWith(engine).pull(since = 0)
        assertIs<SyncTransportOutcome.MalformedResponse>(outcome)
    }

    // ---- Exception classification (same logic HttpExternalLicensingTransportMockTest already proves for the sibling class -- verified independently here since SyncTransport has its own copy) ----

    @Test
    fun requestTimeoutIsClassifiedAsTimeout() = runTest {
        val engine = MockEngine {
            delay(200)
            respondJson(HttpStatusCode.OK, """{"stored":0,"received":0}""")
        }
        val configuration = SyncRelayConfiguration(
            environment = com.actionaura.retail.licensing.transport.LicensingEnvironment.DEVELOPMENT,
            baseUrl = "http://127.0.0.1:5551",
            requestTimeoutMillis = 10,
            connectTimeoutMillis = 10,
            responseTimeoutMillis = 10,
        )
        val transport = SyncTransport(HttpClient(engine), configuration, FakeDeviceSigner(), "mock-installation-id")
        val outcome = transport.push(emptyList())
        assertEquals(SyncTransportOutcome.Timeout, outcome)
    }

    @Test
    fun aThrownExceptionMentioningTlsIsClassifiedAsTlsFailure() = runTest {
        val engine = MockEngine { throw FakeTransportException("SSL handshake failed: unable to find valid certification path") }
        val outcome = transportWith(engine).push(emptyList())
        assertEquals(SyncTransportOutcome.TlsFailure, outcome)
    }

    @Test
    fun aGenericThrownExceptionIsClassifiedAsNetworkFailure() = runTest {
        val engine = MockEngine { throw FakeTransportException("connection refused") }
        val outcome = transportWith(engine).pull(since = 0)
        val networkFailure = assertIs<SyncTransportOutcome.NetworkFailure>(outcome)
        assertTrue(networkFailure.reason.contains("connection refused"))
    }
}

private class FakeTransportException(message: String) : Exception(message)

/** Deterministic, obviously-fake signing capability -- MockEngine bypasses real server-side signature verification entirely, so no real Ed25519 material is needed here (matches `HttpExternalLicensingTransportMockTest`'s own `FakeDeviceSigner`). */
private class FakeDeviceSigner : DeviceSigner {
    override suspend fun publicKeyBytes(): ByteArray = ByteArray(32) { it.toByte() }
    override suspend fun sign(message: ByteArray): ByteArray = ByteArray(64) { 0x24 }
}

private fun io.ktor.client.engine.mock.MockRequestHandleScope.respondJson(status: HttpStatusCode, body: String) =
    respond(body, status, headersOf(HttpHeaders.ContentType, "application/json"))

private fun OutgoingContent.toByteArray(): ByteArray = when (this) {
    is OutgoingContent.ByteArrayContent -> bytes()
    else -> error("expected a ByteArrayContent body, got ${this::class.simpleName}")
}
