package com.actionaura.retail.sync

import com.actionaura.retail.securestorage.SecureBlobStore
import com.actionaura.retail.securestorage.SecureMaterialScope
import com.actionaura.retail.securestorage.SecureMaterialStore
import com.actionaura.retail.securestorage.getOrNull
import io.ktor.client.HttpClient

/**
 * Task 10 (multi-device-sync-foundation) -- closes a real gap Tasks 8/9
 * both explicitly left open in their own reports: neither wired a real,
 * persisted `installationId` into production DI, because none actually
 * existed to wire -- `ActivationViewModel` still only calls the honest
 * `TransportOutcome.TransportNotConfigured` stub
 * (`activateInstallation(ActivationCommand)`), never the one real,
 * wire-verified path (`HttpExternalLicensingTransport.activateWithLicenseKey`),
 * and `SecureMaterialStoreActivationSink` (the one real
 * `commitActivationBundle` caller in this whole module) is never
 * constructed in production either -- confirmed by grepping every
 * commonMain call site of both before writing this file.
 *
 * Investigated (per this task's own brief: "investigate, don't guess")
 * whether a persisted `installationId` could be discovered at all without
 * inventing new persistence architecture, and found one real, existing,
 * previously-unused primitive that makes it possible:
 * [SecureBlobStore.list], a real method on the platform primitive every
 * `AuraAppContainer` already holds, un-called by any production code
 * before this file. `GenerationalSecureMaterialStore.commitActivationBundle`
 * always writes its pointer blob as
 * `"pointer:${scope.productCode}:${scope.installationId}:${scope.accountId ?: "-"}"`
 * (that class's own `pointerName`/`scopeOf`, verified by reading it
 * directly) -- so listing every blob name starting with
 * `"pointer:$productCode:"` and parsing the `installationId` segment back
 * out of the one that exists is a real, non-invented way to discover
 * *which* [SecureMaterialScope] to pass to
 * [SecureMaterialStore.loadActivationBundle] without already knowing its
 * `installationId` up front -- the exact circularity [SecureMaterialScope]
 * itself does not otherwise resolve (its `installationId` field is not
 * optional in practice: `commitActivationBundle`'s own `scopeOf` always
 * fills it from `metadata.ownerInstallationId`, never leaves it null).
 *
 * This device supports at most one activation today (no multi-account
 * concept exists anywhere else in this module either) -- if [list] ever
 * returns more than one matching pointer name (would only happen if a
 * future caller starts committing multiple concurrent activation bundles,
 * not something this module does), the first is used and the rest are
 * silently ignored; not a real scenario this task needs to solve.
 *
 * Real, disclosed, remaining gap (not solved by this file, out of this
 * task's own scope per its brief's "When You're in Over Your Head"
 * section): NOTHING in production yet actually calls
 * `commitActivationBundle` in the first place, so on a real, freshly
 * installed device this will always resolve `null` until a real
 * activation-UI-to-secure-storage wiring task lands (`ActivationViewModel`
 * still defaults to `NoSecureStorageAvailableSink()`). That is a
 * pre-existing gap this task inherited, not one it introduced -- see this
 * task's own report for the full investigation trail. Once *any* real
 * caller commits a bundle (through whatever future mechanism), this
 * resolver picks it up on the very next poll tick with no restart needed,
 * mirroring desktop's own `client_factory` re-resolution rationale
 * (`task-5-report.md`).
 */
suspend fun resolveOwnerInstallationId(
    secureBlobStore: SecureBlobStore,
    secureMaterialStore: SecureMaterialStore,
    productCode: String,
): String? {
    val pointerPrefix = "pointer:$productCode:"
    val pointerNames = secureBlobStore.list(pointerPrefix).getOrNull() ?: return null
    val installationId = pointerNames.firstOrNull()
        ?.removePrefix(pointerPrefix)
        ?.substringBefore(':')
        ?.takeIf { it.isNotBlank() && it != "-" }
        ?: return null

    val scope = SecureMaterialScope(productCode = productCode, installationId = installationId)
    val bundle = secureMaterialStore.loadActivationBundle(scope).getOrNull() ?: return null
    // The bundle's own metadata.ownerInstallationId (not the parsed pointer-name
    // segment) is the authoritative value -- same string in practice, but this
    // avoids ever trusting a value parsed back out of a non-secret blob NAME
    // for anything beyond locating the right scope to load.
    return bundle.metadata.ownerInstallationId
}

/**
 * Real, net-new production construction path for [SyncTransport] -- the
 * one piece Task 9's own report explicitly deferred to this task
 * ("Task 10 ... is expected to do this wiring, supplying a real
 * installationId from wherever the activation flow ends up persisting
 * it"). Returns `null` (never throws, never fabricates a placeholder
 * installationId) whenever either half of what [SyncTransport] needs
 * beyond `deviceSigner` is missing:
 * - [configuration] is `null` -- mirrors desktop's own established
 *   "empty relay URL = inert" pattern (`task-5-report.md`); no relay
 *   base URL has been configured for this build/environment.
 * - [resolveOwnerInstallationId] finds no persisted activation -- this
 *   device has never had a real activation bundle committed (see this
 *   file's own class-level KDoc for why that is real, disclosed,
 *   pre-existing gap, not something this function papers over).
 *
 * [httpClient] MUST already be CIO-engine-backed
 * (`io.ktor.client.engine.cio.CIO`) -- [SyncTransport]'s own class KDoc
 * explains why OkHttp silently drops `pull()`'s GET request body. This
 * function never constructs the `HttpClient` itself (that requires a
 * platform-specific engine dependency `commonMain` does not have on its
 * classpath -- `ktor-client-cio` is androidMain/androidUnitTest-only,
 * confirmed against `shared/build.gradle.kts`) -- the caller (the real
 * platform entry point, e.g. `MainActivity`) constructs it exactly once
 * and passes it in, the same established pattern `AuraAppContainer`
 * already uses for `driverFactory`/`secureBlobStore`.
 */
suspend fun resolveActiveSyncTransport(
    secureBlobStore: SecureBlobStore,
    secureMaterialStore: SecureMaterialStore,
    deviceSigner: DeviceSigner,
    httpClient: HttpClient,
    configuration: SyncRelayConfiguration?,
    productCode: String,
): SyncTransport? {
    if (configuration == null) return null
    val installationId = resolveOwnerInstallationId(secureBlobStore, secureMaterialStore, productCode) ?: return null
    return SyncTransport(httpClient, configuration, deviceSigner, installationId)
}
