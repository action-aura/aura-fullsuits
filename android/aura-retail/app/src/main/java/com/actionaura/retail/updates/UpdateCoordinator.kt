package com.actionaura.retail.updates

import android.content.Context
import android.content.pm.PackageInstaller
import android.util.Log
import com.actionaura.retail.BuildConfig
import com.actionaura.retail.licensing.DeviceIdentity
import com.actionaura.retail.licensing.LicensingCoordinator
import com.actionaura.retail.licensing.OwnerClient
import com.actionaura.retail.licensing.OwnerClientConfig
import com.actionaura.retail.licensing.OwnerClientError
import com.actionaura.retail.server.ServerBootstrap
import com.google.gson.JsonParser
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.util.concurrent.TimeUnit

/** The Owner side of an update, behind an interface so tests can count calls. */
interface ReleaseSource {
    /** Returns Pair(requestId, Owner's raw reply). */
    fun findLatestRelease(installationId: String, appVersion: String, releaseChannel: String?): Pair<String, String>
    fun authorizeReleaseDownload(installationId: String, productVersionId: String): String
    fun downloadRelease(token: String, dest: File, expectedSize: Long, progress: (Long) -> Unit)
}

/** Production [ReleaseSource]: the real [OwnerClient] signing with this device's key. */
class OwnerReleaseSource(private val client: OwnerClient, private val identity: DeviceIdentity) : ReleaseSource {
    override fun findLatestRelease(installationId: String, appVersion: String, releaseChannel: String?) =
        client.findLatestRelease(installationId, appVersion, releaseChannel, identity)

    override fun authorizeReleaseDownload(installationId: String, productVersionId: String) =
        client.authorizeReleaseDownload(installationId, productVersionId, identity)

    override fun downloadRelease(token: String, dest: File, expectedSize: Long, progress: (Long) -> Unit) =
        client.downloadRelease(token, dest, expectedSize, progress)
}

/** The embedded Python backend's side of an update: the ONLY thing that may call an Owner answer an offer. */
interface LocalUpdatesApi {
    fun verifyDescriptor(ownerResponseRaw: String, requestId: String): DescriptorVerdict
}

/**
 * POST /api/updates/_internal/verify-descriptor on the loopback server, with
 * the same X-Aura-Internal-Secret the licensing _internal routes use. The Owner
 * reply goes in as a JSON STRING FIELD (`owner_response_raw`), never as a
 * nested object, so the exact bytes Owner signed reach Python untouched -- the
 * same reason LicensingCoordinator forwards raw bodies (a parse-and-reserialize
 * turns `30` into `30.0` and breaks a genuine signature).
 */
class LocalUpdatesHttp(
    private val baseUrl: () -> String,
    private val secret: () -> String,
    private val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .build(),
) : LocalUpdatesApi {
    override fun verifyDescriptor(ownerResponseRaw: String, requestId: String): DescriptorVerdict {
        val body = com.google.gson.JsonObject().apply {
            addProperty("owner_response_raw", ownerResponseRaw)
            addProperty("request_id", requestId)
        }.toString()
        val request = Request.Builder()
            .url(baseUrl().trimEnd('/') + "/api/updates/_internal/verify-descriptor")
            .header("X-Aura-Internal-Secret", secret())
            .post(body.toRequestBody("application/json".toMediaType()))
            .build()
        return try {
            http.newCall(request).execute().use { response ->
                parseDescriptorVerdict(response.code, response.body?.string() ?: "")
            }
        } catch (_: java.io.IOException) {
            DescriptorVerdict.Refused(UpdateErrorCode.LOCAL_UNAVAILABLE)
        }
    }
}

/**
 * The whole update decision flow with every outside thing injected, so a plain
 * JVM test can drive it against real HTTP servers and real files and COUNT what
 * happened (Owner requests, installer calls). [UpdateCoordinator] is the
 * singleton that wires the real pieces.
 *
 * What it guarantees, in the order a bad day tests it:
 *  - no Owner URL configured -> not one Owner request, ever;
 *  - Python refuses the descriptor -> no download is ever started;
 *  - the finished file is not the size and SHA-256 Owner promised -> deleted;
 *  - the archive is the wrong package / signer / not newer -> deleted;
 *  - the file changed on disk before install -> deleted, installer not called;
 *  - the installer is called only from [install], only with a Ready file.
 */
class UpdateFlow(
    private val ownerBaseUrl: () -> String,
    private val releaseSource: () -> ReleaseSource,
    private val localApi: LocalUpdatesApi,
    private val installationId: suspend () -> String?,
    private val verifier: ApkVerifier,
    private val installer: ApkInstaller,
    private val updatesDir: File,
    private val appVersion: String,
    private val releaseChannel: String = "rc",
    /** Log.e throws in a plain JVM test (the android.jar stubs are not mocked), so it is injected. */
    private val logError: (String, Throwable) -> Unit = { message, cause -> Log.e(TAG, message, cause) },
) {
    private val mutableState = MutableStateFlow<UpdateState>(UpdateState.Idle)
    val state: StateFlow<UpdateState> = mutableState.asStateFlow()

    /** The file+offer an install attempt can fall back to if the user backs out of Android's dialog. */
    @Volatile private var lastReady: UpdateState.Ready? = null

    /**
     * Asks Owner whether a newer build exists. Safe to call at any time: it
     * leaves a download, a ready file or an install in progress alone, because
     * a background tick must never throw away what the user is about to install.
     */
    suspend fun check(): UpdateState = withContext(Dispatchers.IO) {
        when (mutableState.value) {
            is UpdateState.Downloading, is UpdateState.Ready, is UpdateState.NeedsInstallPermission,
            is UpdateState.Installing, is UpdateState.Checking -> return@withContext mutableState.value
            else -> Unit
        }
        // The feature's own off switch, same rule as licensing: an unconfigured
        // build talks to nobody. Checked BEFORE the release source is even built.
        if (ownerBaseUrl().isBlank()) {
            return@withContext set(UpdateState.Disabled(UpdateErrorCode.NOT_CONFIGURED))
        }
        set(UpdateState.Checking)
        try {
            checkOwner()
        } catch (exc: Exception) {
            // Whatever went wrong, "Checking" must not be left as the final
            // word: it would also make every later check a no-op above.
            currentCoroutineContext().ensureActive()
            logError("Update check failed unexpectedly.", exc)
            set(UpdateState.Error(UpdateErrorCode.LOCAL_UNAVAILABLE))
        }
    }

    private suspend fun checkOwner(): UpdateState {
        val install = installationId()
            ?: return set(UpdateState.Disabled(UpdateErrorCode.NOT_ACTIVATED))

        val (requestId, raw) = try {
            releaseSource().findLatestRelease(install, appVersion, releaseChannel)
        } catch (exc: OwnerClientError) {
            return set(UpdateState.Error(UpdateErrorCode.OWNER_UNREACHABLE))
        }
        return when (val verdict = localApi.verifyDescriptor(raw, requestId)) {
            is DescriptorVerdict.Offer -> set(UpdateState.Available(verdict.offer))
            DescriptorVerdict.NoUpdate -> set(UpdateState.UpToDate)
            is DescriptorVerdict.Refused ->
                if (verdict.errorCode == UpdateErrorCode.NOT_ACTIVATED) set(UpdateState.Disabled(verdict.errorCode))
                else set(UpdateState.Error(verdict.errorCode))
        }
    }

    /** Downloads and checks the offered build. Only acts from [UpdateState.Available]. */
    suspend fun download(): UpdateState = withContext(Dispatchers.IO) {
        val offer = (mutableState.value as? UpdateState.Available)?.offer ?: return@withContext mutableState.value
        set(UpdateState.Downloading(0L, offer.sizeBytes))
        clearUpdatesDir()
        // A fixed name, never anything Owner supplied: a path-shaped version or
        // id cannot steer where the file lands.
        val dest = File(updatesDir, "aura-retail-update.apk")
        try {
            val install = installationId() ?: return@withContext set(UpdateState.Error(UpdateErrorCode.NOT_ACTIVATED))
            val source = releaseSource()
            val token = try {
                parseDownloadToken(source.authorizeReleaseDownload(install, offer.productVersionId))
            } catch (exc: OwnerClientError) {
                return@withContext set(UpdateState.Error(UpdateErrorCode.OWNER_UNREACHABLE))
            } ?: return@withContext set(UpdateState.Error(UpdateErrorCode.AUTHORIZE_REFUSED))

            try {
                source.downloadRelease(token, dest, offer.sizeBytes) { received ->
                    mutableState.value = UpdateState.Downloading(received, offer.sizeBytes)
                }
            } catch (exc: OwnerClientError) {
                return@withContext set(
                    UpdateState.Error(
                        if (exc.reasonCode == "DOWNLOAD_TOO_LARGE") UpdateErrorCode.DOWNLOAD_CORRUPT
                        else UpdateErrorCode.DOWNLOAD_FAILED
                    )
                )
            }

            // What arrived must be exactly what the (Python-verified) offer said.
            if (!dest.isFile || dest.length() != offer.sizeBytes ||
                !sha256Hex(dest).equals(offer.checksumSha256, ignoreCase = true)
            ) {
                return@withContext fail(dest, UpdateErrorCode.DOWNLOAD_CORRUPT)
            }
            verifier.verifyArchive(dest, offer)?.let { return@withContext fail(dest, it) }
            val ready = UpdateState.Ready(dest, offer)
            lastReady = ready
            set(ready)
        } catch (exc: Throwable) {
            // Cancellation or anything unforeseen: never leave a candidate behind.
            dest.delete()
            File(dest.path + ".part").delete()
            if (exc is Exception && exc !is kotlinx.coroutines.CancellationException) {
                // "Downloading" must not be the final word (the card would spin forever).
                logError("Download failed unexpectedly.", exc)
                lastReady = null
                set(UpdateState.Error(UpdateErrorCode.DOWNLOAD_FAILED))
            } else {
                throw exc
            }
        }
    }

    /** Hands the Ready file to Android's installer, after the last re-check. */
    suspend fun install(): UpdateState = withContext(Dispatchers.IO) {
        val (file, offer) = when (val s = mutableState.value) {
            is UpdateState.Ready -> s.file to s.offer
            is UpdateState.NeedsInstallPermission -> s.file to s.offer
            else -> return@withContext mutableState.value
        }
        verifier.verifyUnchanged(file, offer)?.let { return@withContext fail(file, it) }
        if (!installer.canRequestPackageInstalls()) {
            return@withContext set(UpdateState.NeedsInstallPermission(file, offer))
        }
        lastReady = UpdateState.Ready(file, offer)
        set(UpdateState.Installing)
        try {
            installer.install(file)
        } catch (exc: Exception) {
            logError("Could not start the install session.", exc)
            currentCoroutineContext().ensureActive()
            return@withContext set(UpdateState.Error(UpdateErrorCode.INSTALL_FAILED))
        }
        mutableState.value
    }

    /** Outcome from [InstallResultReceiver]. */
    fun onInstallStatus(status: Int) {
        when (status) {
            PackageInstaller.STATUS_SUCCESS -> set(UpdateState.UpToDate) // normally the process is replaced first
            // The user closed Android's confirm dialog: nothing is wrong, the
            // file is still good, so offer the same button again.
            PackageInstaller.STATUS_FAILURE_ABORTED -> set(lastReady ?: UpdateState.Idle)
            else -> set(UpdateState.Error(UpdateErrorCode.INSTALL_FAILED))
        }
    }

    private fun set(next: UpdateState): UpdateState {
        mutableState.value = next
        return next
    }

    private fun fail(file: File, code: String): UpdateState {
        file.delete()
        lastReady = null
        return set(UpdateState.Error(code))
    }

    private fun clearUpdatesDir() {
        updatesDir.mkdirs()
        updatesDir.listFiles()?.forEach { it.delete() }
    }

    private companion object {
        const val TAG = "UpdateFlow"
    }
}

/** Reads only `download_token` out of Owner's authorize-download reply; null when absent or blank. */
internal fun parseDownloadToken(raw: String): String? = try {
    JsonParser.parseString(raw).asJsonObject.get("download_token")
        ?.takeUnless { it.isJsonNull }?.asString?.takeIf { it.isNotBlank() }
} catch (_: Exception) {
    null
}

/**
 * Process-wide updater: owns the one [UpdateFlow], the daily background check
 * and the state the Settings card observes. Like [com.actionaura.retail.licensing.LicenseCheckInCoordinator],
 * inert (never starts anything) when `BuildConfig.OWNER_LICENSING_BASE_URL` is blank.
 *
 * The background loop only CHECKS. Downloading and installing a new build is
 * always something an administrator presses a button for.
 */
object UpdateCoordinator {

    private const val TAG = "UpdateCoordinator"

    /** First check a couple of minutes after boot, so it never competes with start-up. */
    const val INITIAL_DELAY_MS: Long = 2L * 60 * 1000

    /** Then once a day. */
    const val INTERVAL_MS: Long = 24L * 60 * 60 * 1000

    @Volatile private var flow: UpdateFlow? = null
    @Volatile private var job: Job? = null
    private val lock = Object()

    private val idleState = MutableStateFlow<UpdateState>(UpdateState.Idle)

    /** What the Settings card renders. Before [start]/[flowFor] runs it is Idle. */
    val state: StateFlow<UpdateState> get() = flow?.state ?: idleState.asStateFlow()

    /** True when this build can update at all (the UI hides the card otherwise). */
    val isConfigured: Boolean get() = BuildConfig.OWNER_LICENSING_BASE_URL.isNotBlank()

    fun flowFor(context: Context): UpdateFlow = synchronized(lock) {
        flow ?: buildFlow(context.applicationContext).also { flow = it }
    }

    private fun buildFlow(appContext: Context): UpdateFlow {
        val identity = DeviceIdentity(File(appContext.filesDir, "data"))
        val licensing = LicensingCoordinator(appContext)
        return UpdateFlow(
            ownerBaseUrl = { BuildConfig.OWNER_LICENSING_BASE_URL },
            releaseSource = {
                OwnerReleaseSource(
                    OwnerClient(OwnerClientConfig(baseUrl = BuildConfig.OWNER_LICENSING_BASE_URL)),
                    identity,
                )
            },
            localApi = LocalUpdatesHttp({ ServerBootstrap.baseUrl() }, { ServerBootstrap.internalSecret() }),
            installationId = { licensing.status()["installation_id"] as? String },
            verifier = ApkVerifier(
                PackageManagerArchiveInspector(appContext),
                appContext.packageName,
                BuildConfig.VERSION_CODE.toLong(),
            ),
            installer = SessionApkInstaller(appContext),
            updatesDir = File(appContext.cacheDir, "updates"),
            appVersion = BuildConfig.VERSION_NAME,
        )
    }

    /**
     * Starts the daily check. Safe to call more than once. Must be called after
     * [ServerBootstrap.start] has completed: every check ends in a call to the
     * embedded server.
     */
    fun start(appContext: Context) {
        if (!isConfigured) return
        synchronized(lock) {
            if (job?.isActive == true) return
            val updateFlow = flowFor(appContext)
            val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
            job = scope.launch {
                delay(INITIAL_DELAY_MS)
                while (isActive) {
                    try {
                        updateFlow.check()
                    } catch (exc: kotlinx.coroutines.CancellationException) {
                        throw exc
                    } catch (exc: Exception) {
                        Log.e(TAG, "Update check failed outright; the next tick will retry.", exc)
                    }
                    delay(INTERVAL_MS)
                }
            }
        }
    }

    fun stop() {
        synchronized(lock) {
            job?.cancel()
            job = null
        }
    }

    fun isRunning(): Boolean = job?.isActive == true

    /** Called by [InstallResultReceiver]. */
    fun onInstallStatus(status: Int) {
        flow?.onInstallStatus(status)
    }
}
