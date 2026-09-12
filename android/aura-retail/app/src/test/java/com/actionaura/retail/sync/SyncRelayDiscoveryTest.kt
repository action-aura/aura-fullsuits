package com.actionaura.retail.sync

import com.google.common.truth.Truth.assertThat
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * Phone-half of launch-readiness sync-relay discovery: precedence between
 * `BuildConfig.OWNER_SYNC_BASE_URL` (an operator's explicit build-time
 * value) and a relay URL persisted at licence activation, plus the
 * transport-safety validation the persisted one must pass. See
 * [SyncCoordinator]'s own class doc for the full design and the one
 * production gap it does NOT close: no embedded-backend route currently
 * returns the persisted `sync_relay_base_url` for a live caller to pass in
 * here, so [SyncCoordinator.start]'s only real call site
 * ([com.actionaura.retail.ui.AppRoot]) always passes `persistedRelayBaseUrl
 * = null`. Everything below exercises the precedence/validation/wiring
 * machinery itself, which is real and independent of that gap.
 *
 * Two layers, mirroring why SyncCoordinatorTest already avoids needing a
 * real Android `Context` (this module has neither Robolectric nor
 * Mockito):
 *  - [SyncCoordinator.resolveRelayBaseUrl] tests exercise the pure
 *    precedence/validation function directly -- proving exactly WHICH url
 *    wins, by asserted string equality.
 *  - `SyncCoordinator.start(identityDir, buildConfigValue,
 *    persistedRelayBaseUrl)` tests exercise the Context-free core of
 *    [SyncCoordinator.start] -- proving the coordinator actually runs (or
 *    stays inert, with no timer ever scheduled) as a real side effect, and
 *    -- via [SyncCoordinator.currentRelayBaseUrl] -- that it is running
 *    against the CORRECT url, not merely *a* valid one.
 */
class SyncRelayDiscoveryTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    @Before
    fun setUp() {
        // Same reason SyncCoordinatorTest swaps this: a rejected discovered
        // URL logs via SyncCoordinator.logError, which defaults to real
        // android.util.Log.e -- "not mocked" in a plain-JVM unit test.
        SyncCoordinator.logError = { _, _ -> }
    }

    @After
    fun tearDown() {
        // SyncCoordinator is a singleton `object` -- every `start()` test
        // below must leave it stopped (running = false, timer cancelled)
        // so it does not leak a live daemon Timer, or a `running = true`
        // state, into whatever test class runs next in the same JVM.
        SyncCoordinator.stop()
        SyncCoordinator.logError = { message, exc ->
            if (exc != null) android.util.Log.e("SyncCoordinator", message, exc)
            else android.util.Log.e("SyncCoordinator", message)
        }
    }

    // ── resolveRelayBaseUrl(): precedence + validation, as pure string-in/string-out ──

    @Test
    fun `blank BuildConfig with a persisted URL resolves to the persisted URL`() {
        val resolved = SyncCoordinator.resolveRelayBaseUrl(
            buildConfigValue = "",
            persistedValue = "https://relay.example.com",
        )

        assertThat(resolved).isEqualTo("https://relay.example.com")
    }

    @Test
    fun `a non-blank BuildConfig wins over a different persisted URL`() {
        val resolved = SyncCoordinator.resolveRelayBaseUrl(
            buildConfigValue = "https://build-time-relay.example.com",
            persistedValue = "https://a-completely-different-relay.example.com",
        )

        assertThat(resolved).isEqualTo("https://build-time-relay.example.com")
    }

    @Test
    fun `blank BuildConfig and no persisted value resolves to blank`() {
        val resolved = SyncCoordinator.resolveRelayBaseUrl(
            buildConfigValue = "",
            persistedValue = null,
        )

        assertThat(resolved).isEmpty()
    }

    @Test
    fun `a persisted value that fails validation resolves to blank, never the unsafe URL`() {
        val resolved = SyncCoordinator.resolveRelayBaseUrl(
            buildConfigValue = "",
            persistedValue = "http://evil.example.com",
        )

        assertThat(resolved).isEmpty()
    }

    // ── start(): the coordinator actually runs against the right url, or stays inert ──

    @Test
    fun `start actually runs against the persisted URL when BuildConfig is blank`() {
        SyncCoordinator.start(tempFolder.newFolder(), "", "https://relay.example.com")

        assertThat(SyncCoordinator.health().running).isTrue()
        assertThat(SyncCoordinator.currentRelayBaseUrl()).isEqualTo("https://relay.example.com")
    }

    @Test
    fun `start actually runs against BuildConfig, not a different persisted URL, when BuildConfig is set`() {
        // The operator-protection half: an explicitly-built relay address
        // must never be silently overridden by one Owner handed this
        // device at activation.
        SyncCoordinator.start(
            tempFolder.newFolder(),
            "https://build-time-relay.example.com",
            "https://a-completely-different-relay.example.com",
        )

        assertThat(SyncCoordinator.health().running).isTrue()
        assertThat(SyncCoordinator.currentRelayBaseUrl()).isEqualTo("https://build-time-relay.example.com")
    }

    @Test
    fun `start stays inert with no timer when BuildConfig is blank and nothing was persisted`() {
        // This contract must not regress: it is SyncCoordinator's entire
        // pre-existing fail-safe-empty behavior, unchanged by this feature.
        //
        // Only `running` is asserted here, not `health().configured` --
        // SyncCoordinator is a singleton `object` and effectiveRelayBaseUrl
        // (which also feeds `configured`) is never cleared by stop(), so a
        // `configured` check here would depend on JVM-wide execution order
        // against this class's OTHER tests (whichever last started
        // successfully). `running` is reset by stop() unconditionally in
        // every test's tearDown and is the load-bearing "did it actually
        // start a timer" signal this test exists to protect.
        SyncCoordinator.start(tempFolder.newFolder(), "", null)

        assertThat(SyncCoordinator.health().running).isFalse()
    }

    @Test
    fun `start stays inert when the only available persisted URL fails validation`() {
        SyncCoordinator.start(tempFolder.newFolder(), "", "http://evil.example.com")

        assertThat(SyncCoordinator.health().running).isFalse()
    }

    @Test
    fun `start is idempotent -- a second call while already running does not swap the active URL`() {
        SyncCoordinator.start(tempFolder.newFolder(), "https://first-relay.example.com", null)
        assertThat(SyncCoordinator.health().running).isTrue()

        // A repeat call while running is documented as a no-op (see
        // SyncCoordinator.start()'s own doc comment) -- a DIFFERENT url on
        // this second call must never take over an already-running loop.
        SyncCoordinator.start(tempFolder.newFolder(), "https://second-relay.example.com", null)

        assertThat(SyncCoordinator.currentRelayBaseUrl()).isEqualTo("https://first-relay.example.com")
    }
}
