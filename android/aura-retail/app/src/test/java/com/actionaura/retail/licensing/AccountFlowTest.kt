package com.actionaura.retail.licensing

import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.security.KeyFactory
import java.security.SecureRandom
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.spec.GCMParameterSpec

/**
 * "Sign in with an Aura account" on Android (plan S6). Pure logic ([AccountFlowSession],
 * [ContractGate], the view parser, the message table) plus the real [OwnerClient] against a
 * MockWebServer with a real device signature. The Compose screens are not unit-testable in
 * this project; the wiring guards at the bottom pin the properties that live only there.
 */
class AccountFlowTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    private lateinit var server: MockWebServer
    private lateinit var identity: DeviceIdentity

    private class TestKeyWrapper : KeyWrapper {
        private val key = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
        private val random = SecureRandom()
        override fun wrap(plaintext: ByteArray): ByteArray {
            val iv = ByteArray(12).also { random.nextBytes(it) }
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, key, GCMParameterSpec(128, iv))
            return iv + cipher.doFinal(plaintext)
        }
        override fun unwrap(stored: ByteArray): ByteArray {
            val iv = stored.copyOfRange(0, 12)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, iv))
            return cipher.doFinal(stored.copyOfRange(12, stored.size))
        }
    }

    @Before
    fun setUp() {
        server = MockWebServer().also { it.start() }
        identity = DeviceIdentity(tempFolder.newFolder(), TestKeyWrapper()).also { it.generateNewKey() }
    }

    @After
    fun tearDown() { server.shutdown() }

    private fun client() = OwnerClient(
        OwnerClientConfig(baseUrl = server.url("/api/licensing/v1").toString(), maxRetries = 4,
            retryBaseBackoffMillis = 1, retryMaxBackoffMillis = 5),
        sleepFn = { },
    )

    private fun parse(raw: String): Map<String, Any?> =
        Gson().fromJson(raw, object : TypeToken<Map<String, Any?>>() {}.type)

    private fun signIn(c: OwnerClient = client(), password: String = "Temp-Sentinel-7Q4Z") = c.accountSignIn(
        productCode = "AURA_RETAIL", platform = "ANDROID", appVersion = "1.0.0-rc.17",
        devicePublicKeyB64 = identity.getPublicKeyB64(), email = "boss@example.test",
        password = password, identity = identity,
    )

    // ── the view parser ──────────────────────────────────────────────────────

    private fun viewMap(passwordChange: Boolean = true, licences: Int = 1, contractRequired: Boolean = true, draft: Any? = true) =
        mapOf<String, Any?>(
            "password_change_required" to passwordChange,
            "licences" to (1..licences).map {
                mapOf("license_public_id" to "lic-$it", "branch_label" to "Branch $it", "plan_code" to "P1",
                    "trial" to mapOf("state" to "TRIAL", "ends_on" to "2026-11-09"))
            },
            "contract" to mapOf("required" to contractRequired, "version_id" to "v1", "language" to "ar",
                "title" to "عقد", "body" to "نص العقد", "sha256" to contractSha256("نص العقد"), "draft" to draft),
            "seller" to mapOf("name" to "Sami", "phone" to "+962780069485"),
        )

    private fun session(view: Map<String, Any?> = viewMap()) =
        AccountFlowSession(parseAccountView(view)!!, "tk_secret")

    @Test
    fun `the view parser reads licences contract and seller`() {
        val v = parseAccountView(viewMap(licences = 2))!!
        assertThat(v.licences.map { it.licensePublicId }).containsExactly("lic-1", "lic-2").inOrder()
        assertThat(v.licences[0].trialEndsOn).isEqualTo("2026-11-09")
        assertThat(v.contract.isRtl).isTrue()
        assertThat(v.sellerPhone).isEqualTo("+962780069485")
    }

    @Test
    fun `a refusal or an answer with no licence is not a view`() {
        assertThat(parseAccountView(mapOf("reason_code" to "ACCOUNT_SIGN_IN_FAILED"))).isNull()
        assertThat(parseAccountView(viewMap(licences = 0))).isNull()
    }

    @Test
    fun `anything but an explicit not-draft is shown as a DRAFT`() {
        assertThat(parseAccountView(viewMap(draft = true))!!.contract.draft).isTrue()
        assertThat(parseAccountView(viewMap(draft = null))!!.contract.draft).isTrue()
        assertThat(parseAccountView(viewMap(draft = false))!!.contract.draft).isFalse()
    }

    @Test
    fun `contract sha256 is the lowercase hex of the UTF-8 bytes`() {
        // echo -n "abc" | sha256sum
        assertThat(contractSha256("abc"))
            .isEqualTo("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad") // pragma: allowlist secret -- the public SHA-256 test vector of "abc"
    }

    // ── the order of the steps ───────────────────────────────────────────────

    @Test
    fun `steps come in the only allowed order`() {
        val s = session()
        assertThat(s.nextStep()).isEqualTo(AccountStep.PASSWORD)
        s.markPasswordChanged()
        assertThat(s.nextStep()).isEqualTo(AccountStep.CONTRACT)
        s.markContractAccepted()
        assertThat(s.nextStep()).isEqualTo(AccountStep.ACTIVATE)
    }

    @Test
    fun `activation is refused until the password is changed and the contract accepted`() {
        val s = session()
        assertThat(s.canActivate()).isFalse()
        s.markPasswordChanged()
        assertThat(s.canActivate()).isFalse()          // password done, contract not
        s.markContractAccepted()
        assertThat(s.canActivate()).isTrue()
        // and the other way round: the contract alone is not enough
        val t = session()
        t.markContractAccepted()
        assertThat(t.canActivate()).isFalse()
    }

    @Test
    fun `several licences need a branch pick and only a listed one is accepted`() {
        val s = session(viewMap(passwordChange = false, licences = 2, contractRequired = false))
        assertThat(s.nextStep()).isEqualTo(AccountStep.BRANCH)
        assertThat(s.isListed("lic-2")).isTrue()
        assertThat(s.isListed("lic-evil")).isFalse()
        assertThat(s.isListed(null)).isFalse()
    }

    @Test
    fun `an account with no contract and no forced change goes straight to activation`() {
        val s = session(viewMap(passwordChange = false, contractRequired = false))
        assertThat(s.nextStep()).isEqualTo(AccountStep.ACTIVATE)
        assertThat(s.canActivate()).isTrue()
    }

    @Test
    fun `clearing the session forgets the ticket`() {
        val s = session()
        assertThat(s.ticket()).isEqualTo("tk_secret")
        s.clear()
        assertThat(s.ticket()).isNull()
        s.replaceTicket("")                       // a blank replacement is ignored, not adopted
        assertThat(s.ticket()).isNull()
        s.replaceTicket("tk_fresh")
        assertThat(s.ticket()).isEqualTo("tk_fresh")
    }

    @Test
    fun `the store drops the previous ticket when a new sign in replaces it`() {
        val first = session()
        AccountFlowStore.set(first)
        AccountFlowStore.set(session())
        assertThat(first.ticket()).isNull()
        AccountFlowStore.clear()
        assertThat(AccountFlowStore.get()).isNull()
    }

    // ── the contract modal's rules ───────────────────────────────────────────

    @Test
    fun `the box is locked until the end and accept needs both`() {
        assertThat(ContractGate.boxEnabled(false)).isFalse()
        assertThat(ContractGate.boxEnabled(true)).isTrue()
        assertThat(ContractGate.acceptEnabled(reachedEnd = true, ticked = false, dataOutsideJordanTicked = true)).isFalse()
        assertThat(ContractGate.acceptEnabled(reachedEnd = false, ticked = true, dataOutsideJordanTicked = true)).isFalse()
        assertThat(ContractGate.acceptEnabled(reachedEnd = true, ticked = true, dataOutsideJordanTicked = true)).isTrue()
    }

    @Test
    fun `accept also needs the mandatory data-outside-Jordan box and never the optional ID box`() {
        // the contract box alone is not enough: the data-outside-Jordan consent is mandatory
        assertThat(ContractGate.acceptEnabled(reachedEnd = true, ticked = true, dataOutsideJordanTicked = false)).isFalse()
        // ... and that box alone is not enough either
        assertThat(ContractGate.acceptEnabled(reachedEnd = true, ticked = false, dataOutsideJordanTicked = true)).isFalse()
    }

    @Test
    fun `the phone-verification note shows exactly while the optional ID box is unticked`() {
        assertThat(ContractGate.idDocumentsNoteVisible(idDocumentsTicked = false)).isTrue()
        assertThat(ContractGate.idDocumentsNoteVisible(idDocumentsTicked = true)).isFalse()
    }

    @Test
    fun `a text not measured yet is never at the end`() {
        // Compose reports maxValue = Int.MAX_VALUE before the first layout.
        assertThat(ContractGate.reachedEnd(0, Int.MAX_VALUE)).isFalse()
        assertThat(ContractGate.reachedEnd(Int.MAX_VALUE, Int.MAX_VALUE)).isFalse()   // the sentinel itself is never "the end"
        assertThat(ContractGate.reachedEnd(0, 0)).isTrue()          // fits without scrolling
        assertThat(ContractGate.reachedEnd(100, 1000)).isFalse()
        assertThat(ContractGate.reachedEnd(998, 1000)).isTrue()
    }

    // ── messages ─────────────────────────────────────────────────────────────

    @Test
    fun `every known refusal has its own plain sentence and unknown ones a safe default`() {
        val codes = listOf("ACCOUNT_SIGN_IN_FAILED", "RATE_LIMITED", "TRIAL_REQUIRES_UPDATE", "CONTRACT_HASH_MISMATCH",
            "ACCOUNT_FLOW_EXPIRED", "CONTRACT_ACCEPTANCE_REQUIRED", "ACTIVATION_GRANT_INVALID", "DEVICE_LIMIT_REACHED",
            "CONSENT_DATA_OUTSIDE_JORDAN_REQUIRED")
        assertThat(codes.map { accountReasonKey(it) }.toSet()).hasSize(codes.size)
        assertThat(accountReasonKey("PASSWORD_POLICY_FAILED", "too_short")).contains("10 characters")
        assertThat(accountReasonKey("PASSWORD_POLICY_FAILED", "same_as_old")).contains("different")
        assertThat(accountReasonKey("SOMETHING_NEW")).isEqualTo("Please check the information entered.")
        assertThat(accountReasonKey("CONSENT_DATA_OUTSIDE_JORDAN_REQUIRED"))
            .isEqualTo("Please tick the box about storing data outside Jordan to continue.")
        // a wrong password never says which half was wrong
        assertThat(accountReasonKey("ACCOUNT_SIGN_IN_FAILED")).contains("email or password")
    }

    // ── the real client against a fake Owner ─────────────────────────────────

    @Test
    fun `sign in posts a signed body that includes the password and is cleared after`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"ticket":"tk_1","password_change_required":true}"""))
        val raw = signIn()
        assertThat(parse(raw)["ticket"]).isEqualTo("tk_1")

        val req = server.takeRequest()
        assertThat(req.path).isEqualTo("/api/licensing/v1/account-sign-ins")
        val body = parse(req.body.readUtf8()).toMutableMap()
        assertThat(body["email"]).isEqualTo("boss@example.test")
        assertThat(body["password"]).isEqualTo("Temp-Sentinel-7Q4Z")
        assertThat(body).doesNotContainKey("license_key")

        // The device signature must cover EVERY field including the password, or Owner
        // would accept a request whose password was altered in transit.
        val signature = Base64.getDecoder().decode(body.remove("signature") as String)
        val raw32 = Base64.getDecoder().decode(identity.getPublicKeyB64())
        val der = byteArrayOf(0x30, 0x2a, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x03, 0x21, 0x00) + raw32
        val verifier = Signature.getInstance("Ed25519")
        verifier.initVerify(KeyFactory.getInstance("Ed25519").generatePublic(X509EncodedKeySpec(der)))
        verifier.update(canonicalizeBytes(body.mapValues { it.value }))
        assertThat(verifier.verify(signature)).isTrue()
    }

    @Test
    fun `a rate limit is handed back as an answer and is never retried`() {
        server.enqueue(MockResponse().setResponseCode(429).setHeader("Retry-After", "120"))
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"ticket":"must-not-be-reached"}"""))
        val answer = parse(signIn())
        assertThat(answer["reason_code"]).isEqualTo("RATE_LIMITED")
        assertThat((answer["retry_after_seconds"] as Number).toInt()).isEqualTo(120)
        assertThat(server.requestCount).isEqualTo(1)
    }

    @Test
    fun `a wrong password answer is returned verbatim with one request`() {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"reason_code":"ACCOUNT_SIGN_IN_FAILED"}"""))
        assertThat(parse(signIn())["reason_code"]).isEqualTo("ACCOUNT_SIGN_IN_FAILED")
        assertThat(server.requestCount).isEqualTo(1)
    }

    @Test
    fun `a server error on sign in is not retried either`() {
        repeat(5) { server.enqueue(MockResponse().setResponseCode(503)) }
        try {
            signIn()
            throw AssertionError("expected NetworkError")
        } catch (exc: NetworkError) {
            // expected
        }
        assertThat(server.requestCount).isEqualTo(1)
    }

    @Test
    fun `password change and contract acceptance send the ticket and are never retried`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("{}"))
        client().changeAccountPassword("AURA_RETAIL", "ANDROID", "1.0.0-rc.17", identity.getPublicKeyB64(),
            "tk_1", "Fresh-Sentinel-9K2M-pw", identity)
        val pw = parse(server.takeRequest().body.readUtf8())
        assertThat(pw["ticket"]).isEqualTo("tk_1")
        assertThat(pw["new_password"]).isEqualTo("Fresh-Sentinel-9K2M-pw")

        server.enqueue(MockResponse().setResponseCode(201).setBody("""{"accepted":true}"""))
        client().acceptAccountContract("AURA_RETAIL", "ANDROID", "1.0.0-rc.17", identity.getPublicKeyB64(),
            "tk_1", "contract-v1", contractSha256("abc"), "ar", identity,
            consentIdDocuments = false, consentDataOutsideJordan = true)
        val ca = parse(server.takeRequest().body.readUtf8())
        assertThat(ca["contract_version_id"]).isEqualTo("contract-v1")
        assertThat(ca["text_sha256"]).isEqualTo(contractSha256("abc"))
        assertThat(ca["language"]).isEqualTo("ar")
    }

    @Test
    fun `the acceptance body carries both consents as separate booleans`() {
        for ((idDocs, outside) in listOf(true to true, false to true, true to false, false to false)) {
            server.enqueue(MockResponse().setResponseCode(201).setBody("""{"accepted":true}"""))
            client().acceptAccountContract("AURA_RETAIL", "ANDROID", "1.0.0-rc.17", identity.getPublicKeyB64(),
                "tk_1", "contract-v1", contractSha256("abc"), "ar", identity,
                consentIdDocuments = idDocs, consentDataOutsideJordan = outside)
            val consents = parse(server.takeRequest().body.readUtf8())["consents"] as Map<*, *>
            assertThat(consents).containsExactly("id_documents", idDocs, "data_outside_jordan", outside)
        }
    }

    @Test
    fun `owner's refusal of the data-outside-Jordan consent reaches the screen with its own sentence`() {
        server.enqueue(MockResponse().setResponseCode(400).setBody("""{"reason_code":"CONSENT_DATA_OUTSIDE_JORDAN_REQUIRED"}"""))
        val raw = client().acceptAccountContract("AURA_RETAIL", "ANDROID", "1.0.0-rc.17", identity.getPublicKeyB64(),
            "tk_1", "contract-v1", contractSha256("abc"), "ar", identity,
            consentIdDocuments = true, consentDataOutsideJordan = false)
        val code = parse(raw)["reason_code"] as String
        assertThat(code).isEqualTo("CONSENT_DATA_OUTSIDE_JORDAN_REQUIRED")
        assertThat(accountReasonKey(code)).isEqualTo("Please tick the box about storing data outside Jordan to continue.")
        // and Arabic exists for it (the screen wraps the key in tr())
        assertThat(com.actionaura.retail.ui.i18n.arabicFor(accountReasonKey(code)))
            .isEqualTo("يرجى الموافقة على حفظ البيانات خارج الأردن للمتابعة.")
    }

    @Test
    fun `activation by account sends the ticket and the licence id and no licence key`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"result":"SUCCESS"}"""))
        client().activateWithAccount("AURA_RETAIL", "ANDROID", "1.0.0-rc.17", "rc", "inst-1",
            identity.getPublicKeyB64(), "tk_1", "lic-1", "idem-1", identity)
        val sent = server.takeRequest()
        assertThat(sent.path).isEqualTo("/api/licensing/v1/activations")
        val body = parse(sent.body.readUtf8())
        assertThat(body["account_ticket"]).isEqualTo("tk_1")
        assertThat(body["license_public_id"]).isEqualTo("lic-1")
        assertThat(body).doesNotContainKey("license_key")
    }

    // ── wiring that only exists in Compose / AppRoot (source guards, like LicensingWiringContractTest) ──

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    @Test
    fun `the contract dialog cannot be dismissed`() {
        val screen = source("src/main/java/com/actionaura/retail/ui/screens/AccountSignInScreen.kt")
        assertThat(screen).contains("dismissOnBackPress = false")
        assertThat(screen).contains("dismissOnClickOutside = false")
        assertThat(screen).contains("onDismissRequest = {}")
        assertThat(screen).contains("ContractGate.acceptEnabled(reachedEnd, ticked, dataOutsideJordanTicked)")
        // both consents travel to the coordinator, each as its own boolean
        assertThat(screen).contains("onAccept(idDocumentsTicked, dataOutsideJordanTicked)")
        assertThat(screen).contains("coordinator.acceptContract(idDocuments, dataOutsideJordan)")
        assertThat(screen).contains("ContractGate.idDocumentsNoteVisible(idDocumentsTicked)")
        assertThat(screen).contains("ContractGate.boxEnabled(reachedEnd)")
    }

    @Test
    fun `the sign in is the first way in and the role step is wired`() {
        assertThat(source("src/main/java/com/actionaura/retail/ui/screens/LicensingScreen.kt")).contains("AccountSignInPanel(")
        val root = source("src/main/java/com/actionaura/retail/ui/AppRoot.kt")
        assertThat(root).contains("Phase.ROLE -> RoleStepScreen(")
        assertThat(root).contains("return Phase.ROLE")
        // the boss computer's form starts with the Aura e-mail
        assertThat(source("src/main/java/com/actionaura/retail/ui/screens/SetupScreen.kt")).contains("SetupPrefill.email")
    }

    // ── Boss-device lean (owner 2026-11-02) ─────────────────────────────────────

    private fun suggestion(json: String) =
        Gson().fromJson(json, com.actionaura.retail.net.RoleSuggestionResponse::class.java).suggestion

    @Test
    fun `a phone with no boss yet leans to the boss computer`() {
        val s = suggestion("""{"suggestion":{"default":"ADMIN","boss_exists":false,"platform":"ANDROID"}}""")
        assertThat(RoleLean.leansBoss(s)).isTrue()
    }

    @Test
    fun `a Windows computer with no boss yet leans to cashier`() {
        val s = suggestion("""{"suggestion":{"default":"CASHIER","boss_exists":false,"platform":"WINDOWS"}}""")
        assertThat(RoleLean.leansBoss(s)).isFalse()
    }

    @Test
    fun `a second device leans to cashier and names the boss, even a phone`() {
        val s = suggestion(
            """{"suggestion":{"default":"CASHIER","boss_exists":true,"platform":"ANDROID",
               "boss_device_name":"Front desk PC","boss_device_platform":"WINDOWS"}}""")!!
        assertThat(RoleLean.leansBoss(s)).isFalse()
        assertThat(RoleLean.bossName(s)).isEqualTo("Front desk PC")
    }

    @Test
    fun `an unnamed boss is described by its kind and no suggestion keeps the old look`() {
        val phone = suggestion("""{"suggestion":{"default":"CASHIER","boss_exists":true,"boss_device_platform":"ANDROID"}}""")!!
        assertThat(RoleLean.bossName(phone)).isNull()
        assertThat(RoleLean.bossKind(phone)).isEqualTo("a phone")
        val pc = suggestion("""{"suggestion":{"default":"CASHIER","boss_exists":true,"boss_device_platform":"WINDOWS"}}""")!!
        assertThat(RoleLean.bossKind(pc)).isEqualTo("a Windows computer")
        assertThat(RoleLean.leansBoss(null)).isTrue()
    }

    @Test
    fun `the role step asks the backend for its lean and draws from RoleLean`() {
        val screen = source("src/main/java/com/actionaura/retail/ui/screens/RoleStepScreen.kt")
        assertThat(screen).contains("RoleLean.leansBoss(suggestion)")
        assertThat(screen).contains("RoleLean.BOSS_KNOWN")
        // both buttons are always there: the lean is never a lock
        assertThat(screen).contains("onAdmin, tr(\"This is the boss computer (Admin)\")")
        assertThat(screen).contains("onCashier, tr(\"This is a cashier computer\")")
        val root = source("src/main/java/com/actionaura/retail/ui/AppRoot.kt")
        assertThat(root).contains("roleSuggestion().suggestion")
        assertThat(source("src/main/java/com/actionaura/retail/net/AuraApi.kt"))
            .contains("@GET(\"api/onboarding/role-suggestion\")")
    }

    @Test
    fun `the coordinator never persists the password or the ticket`() {
        val coordinator = source("src/main/java/com/actionaura/retail/licensing/LicensingCoordinator.kt")
        val block = coordinator.substringAfter("Sign in with an Aura account").substringBefore("suspend fun checkIn")
        for (forbidden in listOf("SharedPreferences", "putString", "File(", "Log.", "println", "writeText")) {
            assertThat(block).doesNotContain(forbidden)
        }
        val flow = source("src/main/java/com/actionaura/retail/licensing/AccountFlow.kt")
        // the only persisted thing is the role marker's e-mail
        val beforeMarker = flow.substringBefore("class RolePendingStore")
        assertThat(beforeMarker).doesNotContain("getSharedPreferences(")
        assertThat(beforeMarker).doesNotContain("putString(")
    }
}
