package com.actionaura.retail.updates

import com.actionaura.retail.licensing.DeviceIdentity
import com.actionaura.retail.licensing.OwnerClient
import com.actionaura.retail.licensing.OwnerClientConfig
import com.google.common.truth.Truth.assertThat
import com.google.gson.JsonParser
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okio.Buffer
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The whole check -> download -> verify -> install flow, driven through the real
 * [UpdateFlow], real [OwnerClient] and real [LocalUpdatesHttp] against two local
 * HTTP servers (one playing Owner, one playing the embedded Python backend), on
 * real files. Only the PackageManager (inspector) and the PackageInstaller are
 * fakes, and the installer fake COUNTS its calls: "never installed" is asserted
 * as a zero, not inferred from a state name.
 */
class UpdateFlowTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    private lateinit var owner: MockWebServer
    private lateinit var local: MockWebServer
    private lateinit var identity: DeviceIdentity

    /** Every request path Owner saw, in order. */
    private val ownerPaths = CopyOnWriteArrayList<String>()
    private val ownerBodies = CopyOnWriteArrayList<String>()
    private val localRequests = CopyOnWriteArrayList<RecordedRequest>()
    private val localBodies = CopyOnWriteArrayList<String>()

    private val apkBytes = "pretend-this-is-an-apk-0123456789".toByteArray()
    private val apkSha = sha256Hex(apkBytes)

    // What each fake server answers; tests overwrite these.
    private var ownerLatestReply = """{"descriptor":"signed-by-owner","size_bytes":34}"""
    private var authorizeReply = """{"download_token":"tok-1"}"""
    private var downloadResponse: () -> MockResponse = { MockResponse().setResponseCode(200).setBody(Buffer().write(apkBytes)) }
    private var localCode = 200
    private var localBody: () -> String = { offerJson() }

    private fun offerJson(
        sha: String = apkSha,
        size: Long = apkBytes.size.toLong(),
        build: String = "10",
    ) = """{"result":"OFFER","offer":{"product_version_id":"pv-9","version":"1.0.0-rc.9","build_number":$build,""" +
        """"checksum_sha256":"$sha","size_bytes":$size,"release_notes":"Faster till.","update_required":false,""" +
        """"below_min_supported":false}}"""

    private class FakeInstaller(var allowed: Boolean = true) : ApkInstaller {
        val installed = mutableListOf<File>()
        override fun canRequestPackageInstalls() = allowed
        override fun install(apk: File) { installed.add(apk) }
    }

    private val pkg = "com.actionaura.retail"
    private val certs = setOf(sha256Hex("cert".toByteArray()))
    private var archive: ArchiveInfo? = ArchiveInfo(pkg, 10, certs)
    private val installer = FakeInstaller()
    private var ownerFactoryCalls = 0

    @Before
    fun setUp() {
        owner = MockWebServer()
        owner.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                ownerPaths.add(request.path!!)
                ownerBodies.add(request.body.readUtf8())
                return when {
                    request.path!!.endsWith("/releases/latest") -> MockResponse().setResponseCode(200).setBody(ownerLatestReply)
                    request.path!!.endsWith("/releases/authorize-download") -> MockResponse().setResponseCode(200).setBody(authorizeReply)
                    request.path!!.contains("/releases/download/") -> downloadResponse()
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        owner.start()
        local = MockWebServer()
        local.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                localRequests.add(request)
                localBodies.add(request.body.readUtf8())
                return MockResponse().setResponseCode(localCode).setBody(localBody())
            }
        }
        local.start()
        identity = DeviceIdentity(tempFolder.newFolder(), UpdatesTestKeyWrapper())
        identity.generateNewKey()
    }

    @After
    fun tearDown() {
        owner.shutdown()
        local.shutdown()
    }

    private fun updatesDir() = tempFolder.root.resolve("cache/updates")

    private fun flow(
        ownerUrl: String = owner.url("/api/licensing/v1").toString(),
        installationId: String? = "inst-1",
        installedVersionCode: Long = 9,
        installedCerts: Set<String> = certs,
        apkInstaller: ApkInstaller = installer,
    ) = UpdateFlow(
        ownerBaseUrl = { ownerUrl },
        releaseSource = {
            ownerFactoryCalls++
            // Deliberately points at the fake Owner whatever ownerUrl says: the
            // blank-URL guard, not an unreachable address, must be what stops
            // the request in the "no URL configured" test.
            OwnerReleaseSource(
                OwnerClient(
                    OwnerClientConfig(
                        baseUrl = owner.url("/api/licensing/v1").toString(),
                        maxRetries = 0,
                    ),
                    sleepFn = {},
                ),
                identity,
            )
        },
        localApi = LocalUpdatesHttp({ local.url("/").toString() }, { "test-secret" }),
        installationId = { installationId },
        verifier = ApkVerifier(
            object : ArchiveInspector {
                override fun inspectArchive(file: File) = archive
                override fun installedCertSha256s() = installedCerts
            },
            pkg,
            installedVersionCode,
        ),
        installer = apkInstaller,
        updatesDir = updatesDir(),
        appVersion = "1.0.0-rc.8",
        logError = { _, _ -> },
    )

    private fun ownerDownloadHits() = ownerPaths.count { it.contains("/releases/download/") }
    private fun ownerAuthorizeHits() = ownerPaths.count { it.endsWith("/releases/authorize-download") }

    private fun readyFlow(apkInstaller: ApkInstaller = installer): UpdateFlow {
        val f = flow(apkInstaller = apkInstaller)
        runBlocking {
            f.check()
            f.download()
        }
        assertThat(f.state.value).isInstanceOf(UpdateState.Ready::class.java)
        return f
    }

    // ── check() ──────────────────────────────────────────────────────────────

    @Test
    fun `no Owner URL means not one request to Owner or to the local backend`() {
        val f = flow(ownerUrl = "")

        runBlocking { f.check() }

        assertThat(f.state.value).isEqualTo(UpdateState.Disabled(UpdateErrorCode.NOT_CONFIGURED))
        assertThat(owner.requestCount).isEqualTo(0)
        assertThat(local.requestCount).isEqualTo(0)
        // The release source was not even built.
        assertThat(ownerFactoryCalls).isEqualTo(0)
    }

    @Test
    fun `a device that is not activated asks nobody`() {
        val f = flow(installationId = null)

        runBlocking { f.check() }

        assertThat(f.state.value).isEqualTo(UpdateState.Disabled(UpdateErrorCode.NOT_ACTIVATED))
        assertThat(owner.requestCount).isEqualTo(0)
        assertThat(local.requestCount).isEqualTo(0)
    }

    @Test
    fun `check hands Python the exact Owner bytes and the id that was signed`() {
        // `30` must stay `30` and the odd spacing must survive: Python verifies
        // a signature over THESE bytes.
        ownerLatestReply = """{"a" :  30,"b":"x"}"""
        val f = flow()

        runBlocking { f.check() }

        assertThat(f.state.value).isInstanceOf(UpdateState.Available::class.java)
        assertThat(local.requestCount).isEqualTo(1)
        val request = localRequests.single()
        assertThat(request.path).isEqualTo("/api/updates/_internal/verify-descriptor")
        assertThat(request.method).isEqualTo("POST")
        assertThat(request.getHeader("X-Aura-Internal-Secret")).isEqualTo("test-secret")
        val sent = JsonParser.parseString(localBodies.single()).asJsonObject
        assertThat(sent.get("owner_response_raw").asString).isEqualTo(ownerLatestReply)
        val signedRequestId = JsonParser.parseString(ownerBodies.single()).asJsonObject.get("request_id").asString
        assertThat(sent.get("request_id").asString).isEqualTo(signedRequestId)
    }

    @Test
    fun `an offer from Python becomes Available with its fields`() {
        val f = flow()

        runBlocking { f.check() }

        val offer = (f.state.value as UpdateState.Available).offer
        assertThat(offer.version).isEqualTo("1.0.0-rc.9")
        assertThat(offer.buildNumber).isEqualTo(10)
        assertThat(offer.checksumSha256).isEqualTo(apkSha)
        assertThat(offer.sizeBytes).isEqualTo(apkBytes.size.toLong())
        assertThat(offer.releaseNotes).isEqualTo("Faster till.")
        // A check alone never touches the download endpoints.
        assertThat(ownerAuthorizeHits()).isEqualTo(0)
        assertThat(ownerDownloadHits()).isEqualTo(0)
    }

    @Test
    fun `NO_UPDATE from Python is UpToDate`() {
        localBody = { """{"result":"NO_UPDATE"}""" }
        val f = flow()

        runBlocking { f.check() }

        assertThat(f.state.value).isEqualTo(UpdateState.UpToDate)
    }

    @Test
    fun `a refused descriptor starts no download, ever`() {
        localCode = 409
        localBody = { """{"result":"ERROR","error_code":"DESCRIPTOR_UNTRUSTED"}""" }
        val f = flow()

        runBlocking {
            f.check()
            f.download()
            f.download()
        }

        assertThat(f.state.value).isEqualTo(UpdateState.Error(UpdateErrorCode.DESCRIPTOR_UNTRUSTED))
        // The one thing Owner saw is the "latest release" question.
        assertThat(ownerPaths.size).isEqualTo(1)
        assertThat(ownerAuthorizeHits()).isEqualTo(0)
        assertThat(ownerDownloadHits()).isEqualTo(0)
        assertThat(updatesDir().listFiles().orEmpty()).isEmpty()
        assertThat(installer.installed).isEmpty()
    }

    @Test
    fun `Owner unreachable is an error and the local backend is not asked`() {
        owner.shutdown()
        val f = flow()

        runBlocking { f.check() }

        assertThat(f.state.value).isEqualTo(UpdateState.Error(UpdateErrorCode.OWNER_UNREACHABLE))
        assertThat(local.requestCount).isEqualTo(0)
    }

    @Test
    fun `the local backend being down is an error not a stuck Checking`() {
        local.shutdown()
        val f = flow()

        runBlocking { f.check() }

        assertThat(f.state.value).isEqualTo(UpdateState.Error(UpdateErrorCode.LOCAL_UNAVAILABLE))
    }

    @Test
    fun `a check while a download is ready leaves it alone and asks nobody`() {
        val f = readyFlow()
        val ownerBefore = owner.requestCount
        val localBefore = local.requestCount
        val readyState = f.state.value

        runBlocking { f.check() }

        assertThat(f.state.value).isEqualTo(readyState)
        assertThat(owner.requestCount).isEqualTo(ownerBefore)
        assertThat(local.requestCount).isEqualTo(localBefore)
    }

    // ── download() ───────────────────────────────────────────────────────────

    @Test
    fun `a good download becomes Ready, the file is the served bytes, and nothing is installed yet`() {
        val f = readyFlow()

        val ready = f.state.value as UpdateState.Ready
        assertThat(ready.file.readBytes()).isEqualTo(apkBytes)
        assertThat(ready.file.parentFile).isEqualTo(updatesDir())
        assertThat(ownerAuthorizeHits()).isEqualTo(1)
        assertThat(ownerDownloadHits()).isEqualTo(1)
        // Downloading is not installing.
        assertThat(installer.installed).isEmpty()
    }

    @Test
    fun `bytes whose SHA-256 is not the offered one are deleted as corrupt`() {
        // Same length, different content: only the hash can tell.
        downloadResponse = { MockResponse().setResponseCode(200).setBody(Buffer().write("X".repeat(apkBytes.size).toByteArray())) }
        val f = flow()

        runBlocking {
            f.check()
            f.download()
        }

        assertThat(f.state.value).isEqualTo(UpdateState.Error(UpdateErrorCode.DOWNLOAD_CORRUPT))
        assertThat(updatesDir().listFiles().orEmpty()).isEmpty()
        assertThat(installer.installed).isEmpty()
    }

    @Test
    fun `a file shorter than the offered size is deleted as corrupt`() {
        downloadResponse = { MockResponse().setResponseCode(200).setBody(Buffer().write(apkBytes.copyOf(10))) }
        val f = flow()

        runBlocking {
            f.check()
            f.download()
        }

        assertThat(f.state.value).isEqualTo(UpdateState.Error(UpdateErrorCode.DOWNLOAD_CORRUPT))
        assertThat(updatesDir().listFiles().orEmpty()).isEmpty()
    }

    @Test
    fun `a server that sends more than promised is refused as corrupt`() {
        downloadResponse = { MockResponse().setResponseCode(200).setBody(Buffer().write(ByteArray(5_000) { 1 })) }
        val f = flow()

        runBlocking {
            f.check()
            f.download()
        }

        assertThat(f.state.value).isEqualTo(UpdateState.Error(UpdateErrorCode.DOWNLOAD_CORRUPT))
        assertThat(updatesDir().listFiles().orEmpty()).isEmpty()
    }

    @Test
    fun `a failed download asks for the file exactly once`() {
        downloadResponse = { MockResponse().setResponseCode(500) }
        val f = flow()

        runBlocking {
            f.check()
            f.download()
        }

        assertThat(f.state.value).isEqualTo(UpdateState.Error(UpdateErrorCode.DOWNLOAD_FAILED))
        assertThat(ownerDownloadHits()).isEqualTo(1)
    }

    @Test
    fun `no download token means no download request`() {
        authorizeReply = """{"result":"REJECTED"}"""
        val f = flow()

        runBlocking {
            f.check()
            f.download()
        }

        assertThat(f.state.value).isEqualTo(UpdateState.Error(UpdateErrorCode.AUTHORIZE_REFUSED))
        assertThat(ownerDownloadHits()).isEqualTo(0)
    }

    // ── every refusal leaves the installer untouched ─────────────────────────

    @Test
    fun `every archive the verifier refuses is deleted and never reaches the installer`() {
        val scenarios = listOf(
            "wrong package" to (ArchiveInfo("com.evil.app", 10, certs) to UpdateErrorCode.WRONG_PACKAGE),
            "other signer" to (ArchiveInfo(pkg, 10, setOf(sha256Hex("evil".toByteArray()))) to UpdateErrorCode.SIGNATURE_MISMATCH),
            "not newer" to (ArchiveInfo(pkg, 9, certs) to UpdateErrorCode.NOT_NEWER),
            "build number differs" to (ArchiveInfo(pkg, 11, certs) to UpdateErrorCode.DESCRIPTOR_INVALID),
        )
        for ((name, scenario) in scenarios) {
            val (info, expected) = scenario
            archive = info
            val f = flow()

            runBlocking {
                f.check()
                f.download()
                f.install()
            }

            assertWithMessage(name, f.state.value).isEqualTo(UpdateState.Error(expected))
            assertThat(updatesDir().listFiles().orEmpty()).isEmpty()
            assertThat(installer.installed).isEmpty()
        }
    }

    private fun assertWithMessage(name: String, value: UpdateState) =
        com.google.common.truth.Truth.assertWithMessage(name).that(value)

    // ── install() ────────────────────────────────────────────────────────────

    @Test
    fun `a good archive is handed to the installer exactly once`() {
        val f = readyFlow()
        val file = (f.state.value as UpdateState.Ready).file

        runBlocking { f.install() }

        assertThat(installer.installed).containsExactly(file)
        assertThat(f.state.value).isEqualTo(UpdateState.Installing)
    }

    @Test
    fun `a file changed on disk after the download is not installed`() {
        val f = readyFlow()
        val file = (f.state.value as UpdateState.Ready).file
        // Same length, one byte different.
        file.writeBytes(apkBytes.copyOf().also { it[0] = (it[0] + 1).toByte() })

        runBlocking { f.install() }

        assertThat(f.state.value).isEqualTo(UpdateState.Error(UpdateErrorCode.INSTALLER_TAMPERED))
        assertThat(file.exists()).isFalse()
        assertThat(installer.installed).isEmpty()
    }

    @Test
    fun `without the install permission the installer is not called, and with it it is`() {
        installer.allowed = false
        val f = readyFlow()

        runBlocking { f.install() }

        assertThat(f.state.value).isInstanceOf(UpdateState.NeedsInstallPermission::class.java)
        assertThat(installer.installed).isEmpty()

        // The allow-half: after the user grants it, the same button works.
        installer.allowed = true
        runBlocking { f.install() }

        assertThat(installer.installed).hasSize(1)
        assertThat(f.state.value).isEqualTo(UpdateState.Installing)
    }

    @Test
    fun `install does nothing unless a verified file is waiting`() {
        val f = flow()

        runBlocking {
            f.install()
            f.check() // Available, still not downloaded
            f.install()
        }

        assertThat(installer.installed).isEmpty()
    }

    @Test
    fun `an installer that throws becomes INSTALL_FAILED and is not reported as installing`() {
        val throwing = object : ApkInstaller {
            override fun canRequestPackageInstalls() = true
            override fun install(apk: File) = throw java.io.IOException("session refused")
        }
        val f = readyFlow(throwing)

        runBlocking { f.install() }

        assertThat(f.state.value).isEqualTo(UpdateState.Error(UpdateErrorCode.INSTALL_FAILED))
    }

    @Test
    fun `backing out of Android's confirm dialog returns to Ready, a real failure is an error`() {
        val f = readyFlow()
        val ready = f.state.value

        runBlocking { f.install() }
        f.onInstallStatus(android.content.pm.PackageInstaller.STATUS_FAILURE_ABORTED)
        assertThat(f.state.value).isEqualTo(ready)

        runBlocking { f.install() }
        f.onInstallStatus(android.content.pm.PackageInstaller.STATUS_FAILURE)
        assertThat(f.state.value).isEqualTo(UpdateState.Error(UpdateErrorCode.INSTALL_FAILED))
    }

    // ── the pure reply parser ────────────────────────────────────────────────

    @Test
    fun `parseDescriptorVerdict fails closed on anything that is not exactly an offer or no-update`() {
        assertThat(parseDescriptorVerdict(200, """{"result":"NO_UPDATE"}""")).isEqualTo(DescriptorVerdict.NoUpdate)
        assertThat(parseDescriptorVerdict(200, offerJson())).isInstanceOf(DescriptorVerdict.Offer::class.java)

        fun refused(code: Int, body: String) = (parseDescriptorVerdict(code, body) as DescriptorVerdict.Refused).errorCode

        for (known in listOf("DESCRIPTOR_UNTRUSTED", "DESCRIPTOR_MISMATCH", "DESCRIPTOR_INVALID", "NOT_ACTIVATED", "OWNER_REFUSED")) {
            assertThat(refused(409, """{"result":"ERROR","error_code":"$known"}""")).isEqualTo(known)
        }
        // An unknown 409 code is not echoed into the UI.
        assertThat(refused(409, """{"error_code":"SOMETHING_NEW"}""")).isEqualTo(UpdateErrorCode.LOCAL_UNAVAILABLE)
        assertThat(refused(401, """{"error":"bad secret"}""")).isEqualTo(UpdateErrorCode.LOCAL_UNAVAILABLE)
        assertThat(refused(500, "oops")).isEqualTo(UpdateErrorCode.LOCAL_UNAVAILABLE)
        assertThat(refused(200, "not json")).isEqualTo(UpdateErrorCode.LOCAL_UNAVAILABLE)
        assertThat(refused(200, """{"result":"MAYBE"}""")).isEqualTo(UpdateErrorCode.DESCRIPTOR_INVALID)
        assertThat(refused(200, """{"result":"OFFER"}""")).isEqualTo(UpdateErrorCode.DESCRIPTOR_INVALID)
        // Missing/zero size or missing checksum: nothing to verify a download against.
        assertThat(refused(200, offerJson(size = 0))).isEqualTo(UpdateErrorCode.DESCRIPTOR_INVALID)
        assertThat(refused(200, offerJson(sha = ""))).isEqualTo(UpdateErrorCode.DESCRIPTOR_INVALID)
        // A null build number is allowed.
        val noBuild = parseDescriptorVerdict(200, offerJson(build = "null")) as DescriptorVerdict.Offer
        assertThat(noBuild.offer.buildNumber).isNull()
    }
}
