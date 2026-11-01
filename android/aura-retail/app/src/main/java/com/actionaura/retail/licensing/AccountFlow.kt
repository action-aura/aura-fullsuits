package com.actionaura.retail.licensing

import android.content.Context
import java.security.MessageDigest

/**
 * "Sign in with your Aura account" (new customer flow, plan S6, 2026-10-31) --
 * the pure, unit-testable half of the Android side. The Windows twin is
 * commercial_runtime/licensing_contracts/{account_flow,account_session,
 * account_routes}.py and licensing-account.js; both read the SAME Owner answer
 * through the SAME Python normaliser (`/_internal/account-sign-in-response`).
 *
 * What lives where on Android: Kotlin holds the device key, so Kotlin makes the
 * signed Owner calls ([OwnerClient]); Python only re-reads Owner's raw sign-in
 * answer (contract text checked against its hash) and, as always, is the only
 * thing that decides whether an ACTIVATION is genuine.
 *
 * THE PASSWORD AND THE TICKET. The typed password exists only as an argument of
 * the coordinator call and inside the request body, and is nulled in a `finally`
 * (OwnerClient). The Owner ticket is a bearer credential for minutes: it is kept
 * in [AccountFlowSession] in MEMORY ONLY -- never SharedPreferences, never a
 * file, never a log line, never a Compose `rememberSaveable`.
 */

/** Everything the screens may know about a sign-in. NO ticket, NO password. */
data class AccountLicence(
    val licensePublicId: String,
    val branchLabel: String?,
    val planCode: String?,
    /** Friendly package name Owner sends next to [planCode] (rc.19); null from an older Owner. */
    val planName: String? = null,
    val planNameAr: String? = null,
    val trialState: String?,
    val trialEndsOn: String?,
)

data class AccountContract(
    val required: Boolean,
    val versionId: String?,
    val language: String?,
    val title: String?,
    val body: String?,
    val sha256: String?,
    /** True when no lawyer has approved this text: the screen shows a DRAFT watermark. */
    val draft: Boolean,
) {
    val isRtl: Boolean get() = (language ?: "ar").lowercase().startsWith("ar")
}

data class AccountView(
    val passwordChangeRequired: Boolean,
    val licences: List<AccountLicence>,
    val contract: AccountContract,
    val sellerName: String?,
    val sellerPhone: String?,
)

/** `plan_name` as Owner may send it: one string (both languages) or {"en": .., "ar": ..}. */
internal fun planNameOf(raw: Any?, lang: String): String? = when (raw) {
    is String -> raw.takeIf { it.isNotBlank() }
    is Map<*, *> -> (raw[lang] as? String)?.takeIf { it.isNotBlank() }
    else -> null
}

/** Local fallback names for the three launch packages -- the same map as licensing-account.js PLAN_NAMES. */
private val PLAN_NAMES: Map<String, Pair<String, String>> = mapOf(
    "AURA_RETAIL_P1_YEARLY" to ("Package 1 (yearly)" to "الباقة 1 (سنوية)"),
    "AURA_RETAIL_P2_MONTHLY_12" to ("Package 2 (monthly, 12-month contract)" to "الباقة 2 (شهرية، عقد 12 شهراً)"),
    "AURA_RETAIL_P3_MONTHLY_24" to ("Package 3 (monthly, 24-month contract)" to "الباقة 3 (شهرية، عقد 24 شهراً)"),
)

/**
 * The name to SHOW for a licence's package: what Owner sent, else the local map of the plan code,
 * else null. A raw code like AURA_RETAIL_P1_YEARLY is never returned -- a code is not a name.
 */
fun planDisplayName(lic: AccountLicence, arabic: Boolean): String? {
    val sent = if (arabic) lic.planNameAr ?: lic.planName else lic.planName ?: lic.planNameAr
    if (sent != null) return sent
    val local = PLAN_NAMES[lic.planCode] ?: return null
    return if (arabic) local.second else local.first
}

/**
 * "<shop or branch label> · <package name>"; the placeholder shows only when both are missing, or always in
 * the branch picker (several licences of one package must stay tellable apart). Same rule as licensing-account.js.
 */
fun licenceDisplayLabel(lic: AccountLicence, fallbackLabel: String, arabic: Boolean, alwaysFallback: Boolean = false): String {
    val parts = listOfNotNull(lic.branchLabel ?: fallbackLabel.takeIf { alwaysFallback }, planDisplayName(lic, arabic))
    return if (parts.isEmpty()) fallbackLabel else parts.joinToString(" · ")
}

/** Steps of the wizard, in the only order they may happen. */
enum class AccountStep { SIGN_IN, PASSWORD, CONTRACT, BRANCH, ACTIVATE }

internal fun contractSha256(body: String): String =
    MessageDigest.getInstance("SHA-256").digest(body.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }

@Suppress("UNCHECKED_CAST")
internal fun parseAccountView(map: Map<String, Any?>): AccountView? {
    if (map["reason_code"] != null) return null
    val licences = (map["licences"] as? List<*>)?.mapNotNull { raw ->
        val m = raw as? Map<String, Any?> ?: return@mapNotNull null
        val id = (m["license_public_id"] as? String)?.takeIf { it.isNotBlank() } ?: return@mapNotNull null
        val trial = m["trial"] as? Map<String, Any?>
        AccountLicence(
            licensePublicId = id,
            branchLabel = m["branch_label"] as? String,
            planCode = m["plan_code"] as? String,
            planName = planNameOf(m["plan_name"], "en") ?: (m["plan_name_en"] as? String)?.takeIf { it.isNotBlank() },
            planNameAr = planNameOf(m["plan_name"], "ar") ?: (m["plan_name_ar"] as? String)?.takeIf { it.isNotBlank() },
            trialState = trial?.get("state") as? String,
            trialEndsOn = trial?.get("ends_on") as? String,
        )
    }.orEmpty()
    if (licences.isEmpty()) return null
    val c = map["contract"] as? Map<String, Any?> ?: emptyMap()
    val seller = map["seller"] as? Map<String, Any?> ?: emptyMap()
    return AccountView(
        passwordChangeRequired = map["password_change_required"] == true,
        licences = licences,
        contract = AccountContract(
            required = c["required"] == true,
            versionId = c["version_id"] as? String,
            language = c["language"] as? String,
            title = c["title"] as? String,
            body = c["body"] as? String,
            sha256 = c["sha256"] as? String,
            draft = c["draft"] != false,   // anything but an explicit "approved" is a draft
        ),
        sellerName = seller["name"] as? String,
        sellerPhone = seller["phone"] as? String,
    )
}

/**
 * The local ORDER of the wizard (password, then contract, then branch, then
 * activate), enforced here AND by Owner (ACTIVATION_GRANT_INVALID /
 * CONTRACT_ACCEPTANCE_REQUIRED): a broken or tampered screen cannot skip a step.
 * Also the in-memory home of the ticket. One instance per sign-in.
 */
class AccountFlowSession(val view: AccountView, ticket: String) {
    private var ticketValue: String? = ticket
    var passwordPending: Boolean = view.passwordChangeRequired
        private set
    var contractAccepted: Boolean = false
        private set

    val contractPending: Boolean get() = view.contract.required && !contractAccepted

    fun ticket(): String? = ticketValue

    fun replaceTicket(fresh: String?) { if (!fresh.isNullOrBlank()) ticketValue = fresh }
    fun markPasswordChanged() { passwordPending = false }
    fun markContractAccepted() { contractAccepted = true }

    fun nextStep(): AccountStep = when {
        passwordPending -> AccountStep.PASSWORD
        contractPending -> AccountStep.CONTRACT
        view.licences.size > 1 -> AccountStep.BRANCH
        else -> AccountStep.ACTIVATE
    }

    fun canActivate(): Boolean = !passwordPending && !contractPending

    /** Only a licence Owner itself listed for THIS sign-in may be sent back. */
    fun isListed(licensePublicId: String?): Boolean =
        licensePublicId != null && view.licences.any { it.licensePublicId == licensePublicId }

    fun clear() { ticketValue = null }
}

/** Process-wide, memory-only home of the current sign-in (see the file comment). */
object AccountFlowStore {
    @Volatile private var current: AccountFlowSession? = null
    fun set(session: AccountFlowSession) { current?.clear(); current = session }
    fun get(): AccountFlowSession? = current
    fun clear() { current?.clear(); current = null }
}

/**
 * The scroll-to-end + tick rules of the contract modal, as pure functions so a
 * plain JVM test can pin them (Compose is not unit-testable in this project).
 * The box is locked until the text has been scrolled to its end; Accept is
 * locked until the box is ticked AND the end was reached.
 *
 * Owner 2026-11-01: two SEPARATE consents sit under the text. "My data is stored outside
 * Jordan" is mandatory (Accept stays locked without it); "Action Aura keeps my ID photos"
 * is optional (unticked means the team verifies the identity by phone instead).
 */
object ContractGate {
    /** Compose's ScrollState reports maxValue = Int.MAX_VALUE until the first layout; that is
     * "not measured yet", never "at the end". A text that fits without scrolling has maxValue 0. */
    fun reachedEnd(value: Int, maxValue: Int, slackPx: Int = 4): Boolean =
        maxValue != Int.MAX_VALUE && value >= maxValue - slackPx

    fun boxEnabled(reachedEnd: Boolean): Boolean = reachedEnd
    fun acceptEnabled(reachedEnd: Boolean, ticked: Boolean, dataOutsideJordanTicked: Boolean): Boolean =
        reachedEnd && ticked && dataOutsideJordanTicked

    /** The optional ID-document box's phone-verification note shows exactly while it is unticked. */
    fun idDocumentsNoteVisible(idDocumentsTicked: Boolean): Boolean = !idDocumentsTicked
}

/** Plain-language message KEY (English; the caller wraps it in tr()) for a refusal. */
fun accountReasonKey(code: String?, policyCode: String? = null): String = when (code) {
    "ACCOUNT_SIGN_IN_FAILED" -> "The email or password is not right. Check them and try again."
    "RATE_LIMITED" -> "Too many attempts. Please wait a few minutes and try again."
    "TRIAL_REQUIRES_UPDATE" ->
        "This version of Aura Retail is too old to start a trial. Please download the newest version from actionaura.me and install it."
    "CONTRACT_HASH_MISMATCH" ->
        "The agreement text could not be checked, so it was not shown. Please try again, or contact support."
    "ACCOUNT_FLOW_EXPIRED" -> "Your sign-in timed out. Please sign in again."
    "CONTRACT_ACCEPTANCE_REQUIRED" -> "The agreement must be accepted before this computer can be activated."
    "CONSENT_DATA_OUTSIDE_JORDAN_REQUIRED" -> "Please tick the box about storing data outside Jordan to continue."
    "ACTIVATION_GRANT_INVALID" ->
        "This computer could not be activated with that account. Sign in again, or contact your seller."
    "DEVICE_LIMIT_REACHED" -> "This account has reached its computer limit. Contact your seller to add a device."
    "PASSWORD_POLICY_FAILED" -> when (policyCode) {
        "too_short" -> "The new password must be at least 10 characters."
        "same_as_old" -> "The new password must be different from your temporary password."
        else -> "That password is not allowed. Choose a longer one you have not used before."
    }
    "NETWORK_UNAVAILABLE", "REQUEST_TIMED_OUT", "TLS_VERIFICATION_FAILED", "SERVICE_TEMPORARILY_UNAVAILABLE" ->
        "Could not reach the licensing service. Check your internet connection and try again."
    else -> "Please check the information entered."
}

const val MIN_NEW_PASSWORD_LENGTH = 10

/**
 * "Which computer is this?" -- asked once after a computer is activated by
 * signing in with an account. Persisted (so a restart between activation and the
 * answer still asks), but it carries only the e-mail, never a credential.
 * Mirrors app-shell.js ROLE_PENDING_KEY on the desktop.
 */
class RolePendingStore(context: Context, private val clock: () -> Long = System::currentTimeMillis) {
    private val prefs = context.getSharedPreferences("aura_first_run", Context.MODE_PRIVATE)

    fun mark(email: String) {
        prefs.edit().putLong(KEY_AT, clock()).putString(KEY_EMAIL, email).apply()
    }

    /** The prefill e-mail ("" when none was recorded) or null when no role step is owed. */
    fun pendingEmail(): String? {
        val at = prefs.getLong(KEY_AT, 0L)
        if (at == 0L) return null
        if (clock() - at > MAX_AGE_MS) { clear(); return null }
        return prefs.getString(KEY_EMAIL, "") ?: ""
    }

    fun clear() { prefs.edit().remove(KEY_AT).remove(KEY_EMAIL).apply() }

    companion object {
        private const val KEY_AT = "role_pending_at"
        private const val KEY_EMAIL = "role_pending_email"
        const val MAX_AGE_MS = 24L * 60 * 60 * 1000
    }
}

/** The e-mail the boss-computer setup form should start with (memory only). */
object SetupPrefill {
    @Volatile var email: String = ""
}
