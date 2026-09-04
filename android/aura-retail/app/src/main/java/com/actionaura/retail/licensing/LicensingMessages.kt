package com.actionaura.retail.licensing

/**
 * The single place that decides what a licensing reason code is allowed to
 * SAY, and what the activation screen is allowed to DO about it.
 *
 * Kotlin counterpart of `products/retail/frontend/licensing.js`'s
 * REASON_MESSAGES / LOCAL_VERIFICATION_REASON_CODES / TRANSIENT_REASON_CODES
 * / reasonMessage() block. It lives here (plain Kotlin, no Compose, no
 * Android framework types) rather than inside LicensingScreen.kt on purpose:
 * the rules below are the part of the activation experience that can actually
 * be wrong, and keeping them out of a @Composable is what makes them
 * unit-testable on the JVM with no device (see LicensingMessagesTest /
 * ActivationPollTest).
 *
 * Nothing here calls tr(). Every string returned is the ENGLISH key; the
 * caller wraps it in tr(...) exactly like LicensingScreen already did, which
 * keeps this object free of Compose state and therefore runnable under plain
 * JUnit.
 */
object LicensingMessages {

    /**
     * Client-LOCAL verification failures: produced entirely on this device
     * AFTER Owner already answered SUCCESS. Verbatim the bucket
     * licensing.js keeps for the same reason, and the reason the Android
     * screen needed one at all: Owner rotates its signing key while this
     * install still ships a stale trust_anchor.json (a condition this
     * project has actually hit on the live droplet), so Owner APPROVES,
     * marks the installation ACTIVE, consumes a paid device slot -- and the
     * assertion then fails verify_assertion() here with
     * UNKNOWN_SIGNING_KEY. Before this bucket existed on Android every one
     * of these fell through to "Double-check the key and try again", which
     * is the precise opposite of the truth: the key is fine, Owner said yes,
     * and re-typing it reproduces the identical message forever.
     *
     * Source of truth: LOCAL_REASON_CODES in
     * commercial_runtime/licensing_contracts/reason_codes.py, minus the
     * transport codes in [TRANSIENT_REASON_CODES] and minus
     * CAPABILITY_DENIED (raised by the capability decorator; /activate never
     * returns it).
     */
    val LOCAL_VERIFICATION_REASON_CODES: Set<String> = setOf(
        "UNSIGNED_RESPONSE_REJECTED",
        "UNKNOWN_SIGNING_KEY",
        "ASSERTION_VERIFICATION_FAILED",
        "ASSERTION_EXPIRED",
        "ASSERTION_NOT_YET_VALID",
        "ASSERTION_PRODUCT_MISMATCH",
        "ASSERTION_PLATFORM_MISMATCH",
        "ASSERTION_INSTALLATION_MISMATCH",
        "ASSERTION_DEVICE_MISMATCH",
        "ASSERTION_FORBIDDEN_FIELD",
        "CLOCK_ROLLBACK_SUSPECTED",
        "LOCAL_STATE_CORRUPT",
    )

    /**
     * The three members of [LOCAL_VERIFICATION_REASON_CODES] a customer can
     * actually resolve without support -- and the ONLY ones for which "check
     * the date and time" is true advice.
     */
    val CLOCK_FIXABLE_REASON_CODES: Set<String> = setOf(
        "ASSERTION_EXPIRED",
        "ASSERTION_NOT_YET_VALID",
        "CLOCK_ROLLBACK_SUSPECTED",
    )

    /**
     * Deliberately says nothing about the key. It is not the key -- Owner
     * said yes.
     *
     * WHY THIS IS SPLIT (2026-09-04). One message used to serve all twelve
     * codes and it told everyone to "check that this device's date and time
     * are correct". For nine of them that is not merely unhelpful, it is
     * false, and it actively misdirects: a real Mi Note 10 stranded on
     * UNKNOWN_SIGNING_KEY sent an investigation to compare clocks -- they
     * matched to the identical epoch second -- while the actual cause was a
     * trust_store.json seeded once in 2026-08 that permanently shadowed every
     * corrected anchor shipped since. The screen cost more time than the bug.
     *
     * So clock advice is now given only where a clock can be the cause. Every
     * other local failure needs support, and gets the reason code to quote:
     * that code is the single fastest route to the cause (it is also written
     * to licensing_events), and withholding it just means someone has to pull
     * a database off the handset to learn it -- which is exactly what
     * happened.
     */
    const val LOCAL_VERIFICATION_MESSAGE_CLOCK: String =
        "Action Aura approved this activation, but this device could not verify the signed licence it " +
            "received, so it has not been applied yet. Your license key is not the problem — do not " +
            "replace it. Check that this device's date and time are correct; if they are, contact support."

    fun localVerificationMessage(reason: String?): String {
        if (reason != null && reason in CLOCK_FIXABLE_REASON_CODES) return LOCAL_VERIFICATION_MESSAGE_CLOCK
        return "Action Aura approved this activation, but this device could not verify the signed licence it " +
            "received, so it has not been applied yet. Your license key is not the problem — do not " +
            "replace it, and re-entering it cannot help. Please contact support and quote this code: " +
            "${reason ?: "UNKNOWN"}."
    }

    /**
     * "We could not get an answer", never "the answer is no". A held
     * (PENDING) activation must survive every one of these untouched:
     * dropping the pending marker on a DNS blip would strand the user back
     * on a key form for a submission Owner is still perfectly willing to
     * approve. Mirrors licensing.js's TRANSIENT_REASON_CODES exactly.
     */
    val TRANSIENT_REASON_CODES: Set<String> = setOf(
        "NETWORK_UNAVAILABLE",
        "REQUEST_TIMED_OUT",
        "TLS_VERIFICATION_FAILED",
        "SERVICE_TEMPORARILY_UNAVAILABLE",
        "SIGNING_KEY_UNAVAILABLE",
        "RATE_LIMITED",
        "MALFORMED_RESPONSE",
        "DEVICE_KEY_UNAVAILABLE",
    )

    /**
     * Every reason code a client can actually receive, mapped to copy that
     * is true for THAT code. Owner-sent codes come from
     * commercial_runtime/licensing_contracts/reason_codes.py's
     * PUBLIC_REASON_CODES (itself the hand-synced subset of
     * owner/app/licensing_service/reason_codes.py that survives
     * to_public_reason_code()); device-local codes come from the same
     * module's LOCAL_REASON_CODES. LicensingMessagesTest reads both Python
     * files directly and fails if this map ever falls behind them, so the
     * coverage cannot silently rot the next time a code is added.
     */
    val REASON_MESSAGES: Map<String, String> = mapOf(
        // ── Already present before this change (kept verbatim) ────────────
        "INVALID_REQUEST" to "Please check the information entered.",
        "ACTIVATION_REJECTED" to
            "This license key could not be activated. Double-check the key and try again, or contact support.",
        "PRODUCT_MISMATCH" to "This license key is not valid for Aura Retail.",
        "PLATFORM_NOT_ALLOWED" to "This license key is not valid for an Android installation.",
        "DEVICE_LIMIT_REACHED" to
            "This license has reached its device limit. Deactivate another device or contact support to add capacity.",
        "RATE_LIMITED" to "Too many attempts. Please wait a moment and try again.",
        "NETWORK_UNAVAILABLE" to
            "Could not reach the licensing service. Check your internet connection and try again.",
        "REQUEST_TIMED_OUT" to "The request timed out. Please try again.",
        "TLS_VERIFICATION_FAILED" to
            "A secure connection to the licensing service could not be established.",
        "SERVICE_TEMPORARILY_UNAVAILABLE" to
            "The licensing service is temporarily unavailable. Please try again shortly.",
        "SIGNING_KEY_UNAVAILABLE" to
            "The licensing service is temporarily unavailable. Please try again shortly.",
        "MALFORMED_RESPONSE" to
            "Received an unexpected response from the licensing service. Please try again.",
        "DEVICE_KEY_UNAVAILABLE" to "This device is not yet set up for activation. Please try again.",

        // ── Terminal installation states ──────────────────────────────────
        // Reachable now that Owner refuses to re-activate a terminal
        // installation (its activation.py DEACTIVATED/REPLACED guard). This
        // is what a DECLINED activation looks like from here, and it is
        // exactly where the old "double-check the key" fallback did the most
        // damage: the key is fine, the decision was not about the key, and
        // retrying can only ever produce the same answer.
        "INSTALLATION_DEACTIVATED" to
            "This installation was not approved, or has since been deactivated by Action Aura. " +
                "Re-entering the same key will not change that — please contact support to have this device re-enabled.",
        "INSTALLATION_REPLACED" to
            "This installation has been replaced by another device. " +
                "Please contact support if this device still needs access.",
        "INSTALLATION_SUSPENDED" to
            "This installation has been suspended by Action Aura. Re-entering the same key will not change that — " +
                "please contact support.",
        "INSTALLATION_NOT_FOUND" to
            "The licensing service has no record of this installation. Activate this device again with your license key.",

        // ── Device-key family (Owner rejecting THIS device, not the key) ──
        "DEVICE_KEY_MISMATCH" to
            "This device's security key no longer matches the one registered for this installation. " +
                "Please contact support to re-enable this device.",
        "DEVICE_KEY_REVOKED" to
            "This device's security key has been revoked by Action Aura. Please contact support.",
        "DEVICE_ALREADY_REGISTERED" to
            "This device is already registered against this license. No further action is needed.",
        "INVALID_PUBLIC_KEY" to
            "This device's security key was not accepted by the licensing service. Please contact support.",
        "INVALID_SIGNATURE" to
            "This device could not prove its identity to the licensing service. Please try again; if it keeps " +
                "happening, contact support.",

        // ── Build/version eligibility ─────────────────────────────────────
        "RELEASE_CHANNEL_NOT_ALLOWED" to
            "Your license does not cover this release channel of the app. Install the build your license covers, " +
                "or contact support.",
        "VERSION_NOT_ALLOWED" to
            "Your license does not cover this version of the app. Please update the app, or contact support.",
        "VERSION_UNSUPPORTED" to
            "This version of the app is no longer supported. Please update the app and try again.",
        "UNSUPPORTED_CONTRACT_VERSION" to
            "This version of the app is too old to talk to the licensing service. Please update the app.",

        // ── Request-level rejections ──────────────────────────────────────
        // Clock is named explicitly: unlike almost everything else on this
        // list, the customer can actually fix it themselves.
        "INVALID_TIMESTAMP" to
            "This device's date and time could not be read correctly. Check the date and time settings, then try again.",
        "TIMESTAMP_OUTSIDE_ALLOWED_WINDOW" to
            "This device's date and time are too far from the licensing service's. Correct the date and time on " +
                "this device, then try again.",
        "NONCE_REUSED" to
            "A previous attempt was already processed by the licensing service. Please try again.",
        "IDEMPOTENCY_CONFLICT" to
            "A different request was already processed under the same reference. Please try again.",
        "PAYLOAD_TOO_LARGE" to
            "The licensing service rejected this request as too large. Please contact support.",
        "INTERNAL_DECISION_FAILURE" to
            "The licensing service could not complete this request. Please try again shortly; if it keeps " +
                "happening, contact support.",

        // ── Device-local, non-verification ────────────────────────────────
        // Raised by the capability decorator, never by /activate -- listed so
        // a screen that ever surfaces one has honest copy for it.
        "CAPABILITY_DENIED" to "This action is not available in the current licensing state.",
    )

    /**
     * The honest fallback. licensing.js falls back to ACTIVATION_REJECTED's
     * "double-check the key" copy for anything it does not recognise; that is
     * exactly the wrong advice for a code nobody has written copy for yet,
     * because it asserts a cause ("your key") this build has no way to know.
     * Naming the raw code instead costs the user nothing and gives support a
     * real handle -- and, crucially, does not send them to re-type a key that
     * may be perfectly good.
     *
     * `%s` is the reason code. Formatted AFTER tr(), same convention as the
     * rest of the app's templated strings (see Strings.kt's tr() doc).
     */
    const val UNKNOWN_REASON_TEMPLATE: String =
        "This request could not be completed, and this version of the app does not recognise the reason " +
            "the licensing service gave (%s). Do not assume your license key is wrong — please contact " +
            "support and quote that code."

    fun isTransient(reason: String?): Boolean = reason != null && reason in TRANSIENT_REASON_CODES

    fun isLocalVerificationFailure(reason: String?): Boolean =
        reason != null && reason in LOCAL_VERIFICATION_REASON_CODES

    /**
     * The English message for [reason], or null when this build has no copy
     * for it -- callers must then render [UNKNOWN_REASON_TEMPLATE] rather
     * than substituting a message that claims a different cause.
     */
    fun reasonMessage(reason: String?): String? {
        if (reason == null) return null
        if (reason in LOCAL_VERIFICATION_REASON_CODES) return localVerificationMessage(reason)
        return REASON_MESSAGES[reason]
    }
}

/**
 * What one poll of a held (PENDING) activation means. Modelled as a closed
 * hierarchy rather than a bag of booleans so [classifyActivationResult]'s
 * `when` is exhaustive at the call site -- a new outcome cannot be added
 * without the screen being forced to decide what it does about it.
 */
sealed interface ActivationOutcome {
    /** Owner approved AND this device verified and persisted the assertion. */
    data object Approved : ActivationOutcome

    /** Owner is still holding it for a human. Stay on the awaiting screen. */
    data class StillPending(val installationId: String?) : ActivationOutcome

    /**
     * No answer was obtained (offline, timeout, rate limit...). NOT a verdict:
     * the held activation stays held, the marker stays, the timer keeps
     * running, and an automatic tick stays silent about it.
     */
    data class Transient(val reason: String) : ActivationOutcome

    /**
     * Owner said yes and this device could not verify or persist that answer.
     * Also NOT a verdict, but unlike [Transient] it needs someone to act, so
     * the screen speaks up on every tick. Self-heals without the user
     * touching anything (a refreshed trust anchor, a corrected clock), which
     * is why the timer keeps running.
     */
    data class LocalVerificationFailed(val reason: String) : ActivationOutcome

    /** A real verdict from Owner. Stop polling and retire the marker. */
    data class Declined(val reason: String) : ActivationOutcome
}

/**
 * Did this tick actually get an answer out of the licensing service?
 *
 * The one thing the awaiting-approval screen must not get wrong. It shows
 * "Still waiting for approval. Last checked at HH:MM" -- a claim that the
 * service was CONTACTED at that time. Advancing that clock on a tick that
 * never left the device tells the user their licence was verified minutes
 * ago when in fact nothing has been verified since the last time they had
 * signal, which is precisely the class of comfortable lie the rest of this
 * screen was rewritten to remove.
 *
 * Every outcome except [ActivationOutcome.Transient] involved a real answer:
 * [ActivationOutcome.LocalVerificationFailed] in particular DID reach Owner
 * (Owner said yes; this device could not verify it), so it counts.
 * [ActivationOutcome.Transient] is by definition "no answer was obtained".
 *
 * Also drives the poll's backoff -- see [ActivationPollSchedule].
 */
val ActivationOutcome.reachedOwner: Boolean
    get() = this !is ActivationOutcome.Transient

/**
 * Maps a raw [LicensingCoordinator.activate] result map onto the one action
 * the awaiting-approval screen should take.
 *
 * The three-way split between Transient / LocalVerificationFailed / Declined
 * is the whole point: the transport gives the caller NO other way to tell
 * them apart, because routes.py's `/_internal/sync-activation` turns every
 * ActivationFailed into the same `{reason_code}` body whether the code came
 * from Owner or from our own verify_assertion().
 */
fun classifyActivationResult(result: Map<String, Any?>): ActivationOutcome {
    val reason = result["reason_code"] as? String
    return when {
        result["result"] == "SUCCESS" -> ActivationOutcome.Approved
        result["result"] == "PENDING" -> ActivationOutcome.StillPending(result["installation_id"] as? String)
        LicensingMessages.isTransient(reason) -> ActivationOutcome.Transient(reason!!)
        LicensingMessages.isLocalVerificationFailure(reason) -> ActivationOutcome.LocalVerificationFailed(reason!!)
        // A missing reason_code lands here deliberately. It is the one shape
        // that must never be read as "still pending" or "fine": an answer we
        // cannot classify has to stop the poll and be shown, not loop.
        else -> ActivationOutcome.Declined(reason ?: "ACTIVATION_REJECTED")
    }
}
