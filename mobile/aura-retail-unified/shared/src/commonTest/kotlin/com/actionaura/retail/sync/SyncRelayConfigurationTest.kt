package com.actionaura.retail.sync

import com.actionaura.retail.licensing.transport.LicensingEnvironment
import kotlin.test.Test
import kotlin.test.assertIs

/**
 * Task 9 (multi-device-sync-foundation) -- real regression coverage for
 * [SyncRelayConfiguration.validate], mirroring
 * `M9OrchestrationTest`'s own coverage of the sibling
 * `ExternalApiConfiguration.validate` this was modeled on. Not exercised
 * anywhere else -- [SyncTransport] itself never calls `validate()`
 * internally (same as `HttpExternalLicensingTransport`'s own established
 * pattern: validation is a caller-invoked gate, e.g. at a settings screen
 * or DI composition boundary, not baked into the transport).
 */
class SyncRelayConfigurationTest {

    @Test
    fun cleartextHttpInProductionIsInvalid() {
        val config = SyncRelayConfiguration(environment = LicensingEnvironment.PRODUCTION, baseUrl = "http://relay.example.com")
        assertIs<SyncRelayConfigurationValidationResult.Invalid>(config.validate())
    }

    @Test
    fun cleartextHttpOutsideProductionOnAnExplicitLocalDevelopmentHostIsValid() {
        val config = SyncRelayConfiguration(environment = LicensingEnvironment.DEVELOPMENT, baseUrl = "http://127.0.0.1:5551")
        assertIs<SyncRelayConfigurationValidationResult.Valid>(config.validate())
    }

    @Test
    fun cleartextHttpOutsideProductionOnANonLocalHostIsInvalid() {
        val config = SyncRelayConfiguration(environment = LicensingEnvironment.STAGING, baseUrl = "http://staging-relay.example.com")
        assertIs<SyncRelayConfigurationValidationResult.Invalid>(config.validate())
    }

    @Test
    fun httpsInProductionIsValid() {
        val config = SyncRelayConfiguration(environment = LicensingEnvironment.PRODUCTION, baseUrl = "https://relay.example.com")
        assertIs<SyncRelayConfigurationValidationResult.Valid>(config.validate())
    }

    @Test
    fun embeddedCredentialsAreInvalid() {
        val config = SyncRelayConfiguration(environment = LicensingEnvironment.PRODUCTION, baseUrl = "https://user:pass@relay.example.com")
        assertIs<SyncRelayConfigurationValidationResult.Invalid>(config.validate())
    }

    @Test
    fun embeddedFragmentIsInvalid() {
        val config = SyncRelayConfiguration(environment = LicensingEnvironment.PRODUCTION, baseUrl = "https://relay.example.com#frag")
        assertIs<SyncRelayConfigurationValidationResult.Invalid>(config.validate())
    }

    @Test
    fun embeddedQueryStringIsInvalid() {
        val config = SyncRelayConfiguration(environment = LicensingEnvironment.PRODUCTION, baseUrl = "https://relay.example.com?x=1")
        assertIs<SyncRelayConfigurationValidationResult.Invalid>(config.validate())
    }

    @Test
    fun nonPositiveTimeoutIsInvalid() {
        val config = SyncRelayConfiguration(environment = LicensingEnvironment.PRODUCTION, baseUrl = "https://relay.example.com", requestTimeoutMillis = 0)
        assertIs<SyncRelayConfigurationValidationResult.Invalid>(config.validate())
    }
}
