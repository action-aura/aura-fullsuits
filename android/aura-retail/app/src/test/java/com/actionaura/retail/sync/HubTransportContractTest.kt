package com.actionaura.retail.sync

import com.actionaura.retail.ui.codeOnly
import com.google.common.truth.Truth.assertThat
import org.junit.After
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import java.io.File

/**
 * LAN hub transport wiring: pairing to a hub (see [HubPrefs]/[SpkiPinning])
 * must (a) actually outrank the cloud relay in [SyncCoordinator
 * .resolveRelayBaseUrl]'s precedence, and (b) actually pin BOTH of
 * [SyncRelayClient]'s independent TLS seams once it does -- not just the
 * OkHttp one `push()` uses. Both halves have a real, cheap way to be gotten
 * wrong silently (get the precedence order backwards; wire the pinned
 * factory into `httpClient` alone and forget `sslSocketFactory`), so this
 * file exists specifically to catch each one, mutation-proved below in this
 * class's own report rather than merely asserted once and trusted.
 *
 * No Compose test runner, no Chaquopy, and -- for the TLS half specifically
 * -- no harness in this module capable of completing a real SSLSocket
 * handshake and inspecting what got negotiated afterward (SyncRelayClientTest's
 * FakeSyncServer/MockWebServer-style doubles prove the socket ends up
 * connected and readable, not what SSLParameters were set on it before
 * `startHandshake()`). The TLS-shape assertions below are therefore
 * source-text contract checks instead -- the same idiom this module already
 * uses for "does the code have shape X" questions with no runtime harness
 * available (EmployeesWiringContractTest, SyncStatusPresentationTest,
 * ColorTokenContractTest), via the shared [codeOnly] helper in
 * ui/KotlinSourceText.kt.
 */
class HubTransportContractTest {

    @Before
    fun setUp() {
        // resolveRelayBaseUrl() logs via SyncCoordinator.logError when a
        // candidate fails requireTransportIsSafe (see the rejection test
        // below), which defaults to real android.util.Log.e -- "not mocked"
        // in this plain-JVM unit test module. Same swap SyncRelayDiscoveryTest
        // and SyncCoordinatorTest both make, for the identical reason.
        SyncCoordinator.logError = { _, _ -> }
    }

    @After
    fun tearDown() {
        // SyncCoordinator is a singleton `object` -- this field would
        // otherwise leak a test double into whatever runs next in the same
        // JVM. This class never calls SyncCoordinator.start()/stop(), so
        // there is no `running`/timer state to reset here, unlike
        // SyncRelayDiscoveryTest and SyncCoordinatorTest.
        SyncCoordinator.logError = { message, exc ->
            if (exc != null) android.util.Log.e("SyncCoordinator", message, exc)
            else android.util.Log.e("SyncCoordinator", message)
        }
    }

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return codeOnly(file.readText())
    }

    // ── resolveRelayBaseUrl: a paired hub outranks BuildConfig AND the persisted url ──

    @Test
    fun `resolveRelayBaseUrl returns the hub url when all three sources are present`() {
        val resolved = SyncCoordinator.resolveRelayBaseUrl(
            buildConfigValue = "https://build-time-relay.example.com",
            persistedValue = "https://persisted-relay.example.com",
            hubValue = "https://hub.local:5443",
        )

        assertThat(resolved).isEqualTo("https://hub.local:5443")
    }

    @Test
    fun `resolveRelayBaseUrl returns BuildConfig when no hub is paired -- existing behavior unchanged`() {
        val resolved = SyncCoordinator.resolveRelayBaseUrl(
            buildConfigValue = "https://build-time-relay.example.com",
            persistedValue = "https://persisted-relay.example.com",
            hubValue = null,
        )

        assertThat(resolved).isEqualTo("https://build-time-relay.example.com")
    }

    @Test
    fun `resolveRelayBaseUrl returns the persisted url when neither hub nor BuildConfig is set`() {
        val resolved = SyncCoordinator.resolveRelayBaseUrl(
            buildConfigValue = "",
            persistedValue = "https://persisted-relay.example.com",
            hubValue = null,
        )

        assertThat(resolved).isEqualTo("https://persisted-relay.example.com")
    }

    @Test
    fun `a hub url that fails requireTransportIsSafe is rejected, never used`() {
        val resolved = SyncCoordinator.resolveRelayBaseUrl(
            buildConfigValue = "https://build-time-relay.example.com",
            persistedValue = null,
            hubValue = "http://192.168.1.5:5443",
        )

        // Falls back to the next precedence level rather than trusting an
        // unsafe hub URL -- the unsafe candidate itself must never come back.
        assertThat(resolved).isNotEqualTo("http://192.168.1.5:5443")
        assertThat(resolved).isEqualTo("https://build-time-relay.example.com")
    }

    // ── SyncRelayClient: verifyHostname gates endpointIdentificationAlgorithm ──

    /**
     * No live TLS harness exists in this plain-JVM test module to complete a
     * real SSLSocket handshake and inspect its negotiated SSLParameters
     * afterward, so this is a source-text contract check instead (see this
     * class's own doc comment). It is bounded to the ~400 characters
     * immediately before the assignment -- not merely "does the word
     * verifyHostname appear anywhere in the file" -- so this cannot be
     * satisfied by the constructor parameter's own declaration or its doc
     * comment; it must be the code that actually guards the assignment.
     */
    @Test
    fun `executeGetWithBody does not set endpointIdentificationAlgorithm unconditionally`() {
        val code = source("src/main/java/com/actionaura/retail/sync/SyncRelayClient.kt")

        val assignmentIndex = code.indexOf("""endpointIdentificationAlgorithm = "HTTPS"""")
        assertThat(assignmentIndex).isGreaterThan(-1)

        val guardWindow = code.substring(maxOf(0, assignmentIndex - 400), assignmentIndex)
        assertThat(guardWindow).contains("createSocket")
        assertThat(guardWindow).contains("if (verifyHostname)")
    }

    // ── SyncCoordinator.relayClient: both TLS seams pinned ───────────────

    /**
     * THE SINGLE MOST IMPORTANT TEST IN THIS FILE.
     *
     * [SyncRelayClient] has TWO independent TLS paths to the same relay:
     * `push()` goes through OkHttp; `pull()` hand-rolls a raw `Socket`
     * wrapped in whatever `SSLSocketFactory` it was constructed with, and
     * NEVER goes near OkHttp at all (see that class's own class doc, and
     * [SpkiPinning]'s class doc for the full reasoning). Pinning only the
     * OkHttp client -- wiring [SpkiPinning]'s pinned pair into `httpClient`
     * alone and leaving `sslSocketFactory` at its untouched default (the
     * real platform trust store) -- would look correct in casual testing,
     * because `push()` to a self-signed hub would fail loudly and the
     * mistake would seem caught, while actually leaving `pull()` -- the
     * direction that carries the ENTIRE pulled event stream back from the
     * hub, unsigned -- completely open to LAN injection. This test exists
     * so that exact regression can never ship silently.
     */
    @Test
    fun `relayClient threads the pinned SPKI factory into BOTH SyncRelayClient TLS seams`() {
        val code = source("src/main/java/com/actionaura/retail/sync/SyncCoordinator.kt")

        val body = code.substringAfter("private fun relayClient(").substringBefore("private fun pushOnce(")
        assertThat(body).contains("SpkiPinning.pinnedPair(")
        // The httpClient seam -- push(), OkHttp.
        assertThat(body).contains("httpClient = pinnedHttpClient")
        // The sslSocketFactory seam -- pull(), the raw Socket path. THIS is
        // the assertion the "pin OkHttp only" mutation proof targets.
        assertThat(body).contains("sslSocketFactory = factory")
        assertThat(body).contains("verifyHostname = false")
    }
}
