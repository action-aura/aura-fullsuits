package com.actionaura.retail.licensing

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Conformance guard for the Android activation screen's reason-code copy.
 *
 * Reads the AUTHORITATIVE vocabularies straight out of the Python that
 * defines them -- `commercial_runtime/licensing_contracts/reason_codes.py`
 * (what a product client can receive) and
 * `owner/app/licensing_service/reason_codes.py` (what Owner is allowed to
 * emit externally) -- rather than restating them here, so this test fails the
 * next time a code is added to either module and Android's copy is not
 * updated. Same "read the real source of truth" shape as
 * ReadinessContractTest's main.py guard.
 *
 * Why it exists: LicensingScreen used to carry 13 hand-written messages and
 * fall back to ACTIVATION_REJECTED's "Double-check the key and try again"
 * for everything else. That fallback is the exact wrong advice for the codes
 * it was actually hitting -- UNKNOWN_SIGNING_KEY, the ASSERTION_* family,
 * INSTALLATION_DEACTIVATED, INSTALLATION_REPLACED -- where Owner APPROVED the
 * activation (or ruled on it deliberately) and the key was never the problem.
 * Retyping it reproduces the identical message forever.
 */
class LicensingMessagesTest {

    // Gradle unit tests run with the module directory (android/aura-retail/app)
    // as the working directory -- see ReadinessContractTest -- so three levels
    // up is the aura-fullsuits root.
    private val suiteRoot = File("../../..")

    private fun frozensetMembers(text: String, name: String): Set<String> {
        val start = text.indexOf("$name = frozenset(")
        if (start < 0) return emptySet()
        var depth = 0
        var i = text.indexOf('(', start)
        val open = i
        while (i < text.length) {
            when (text[i]) {
                '(' -> depth++
                ')' -> { depth--; if (depth == 0) break }
            }
            i++
        }
        val block = text.substring(open, minOf(i + 1, text.length))
        // Strip comment lines first: the modules explain their normalization
        // rules in prose that names codes ("distinguishing LICENSE_NOT_FOUND
        // from LICENSE_SUSPENDED..."), and those must not be read as members.
        val code = block.lineSequence().filterNot { it.trimStart().startsWith("#") }.joinToString("\n")
        return Regex("\"([A-Z][A-Z0-9_]+)\"").findAll(code).map { it.groupValues[1] }.toSet()
    }

    @Test
    fun every_reason_code_a_client_can_receive_has_its_own_message() {
        val file = File(suiteRoot, "commercial_runtime/licensing_contracts/reason_codes.py")
        assumeTrue("commercial_runtime reason_codes.py not reachable from this run context", file.exists())
        val text = file.readText()

        val success = frozensetMembers(text, "SUCCESS_CODES")
        val public = frozensetMembers(text, "PUBLIC_REASON_CODES")
        val local = frozensetMembers(text, "LOCAL_REASON_CODES")
        assertThat(public).isNotEmpty()
        assertThat(local).isNotEmpty()

        // Success codes are not failures and need no failure copy.
        val mustExplain = (public + local) - success
        val missing = mustExplain.filter { LicensingMessages.reasonMessage(it) == null }.sorted()
        assertThat(missing).isEmpty()
    }

    @Test
    fun owner_public_activation_codes_are_covered_too() {
        // Owner's own public set carries codes commercial_runtime's
        // hand-synced copy does not (DEVICE_ALREADY_REGISTERED) -- a client
        // can still be handed one, so it must not fall through to the
        // fallback either. The download-token / release codes are excluded:
        // they belong to the release-download API this app never calls.
        val file = File(suiteRoot, "owner/app/licensing_service/reason_codes.py")
        assumeTrue("owner reason_codes.py not reachable from this run context", file.exists())
        val text = file.readText()

        val success = frozensetMembers(text, "SUCCESS_CODES")
        val notActivationRelated = setOf(
            "RELEASE_NOT_AVAILABLE", "TOKEN_NOT_FOUND", "TOKEN_EXPIRED",
            "TOKEN_ALREADY_USED", "TOKEN_REVOKED", "ARTIFACT_UNAVAILABLE",
        )
        val mustExplain = frozensetMembers(text, "PUBLIC_REASON_CODES") - success - notActivationRelated
        val missing = mustExplain.filter { LicensingMessages.reasonMessage(it) == null }.sorted()
        assertThat(missing).isEmpty()
    }

    @Test
    fun owner_approved_but_locally_unverifiable_codes_never_blame_the_license_key() {
        // The whole point of the LOCAL_VERIFICATION bucket. Owner said yes;
        // this device could not verify the signed assertion. Telling the user
        // to re-check their key here is actively false and unfixable by them.
        for (code in LicensingMessages.LOCAL_VERIFICATION_REASON_CODES) {
            val message = LicensingMessages.reasonMessage(code)
            assertThat(message).isEqualTo(LicensingMessages.localVerificationMessage(code))
            assertThat(message).doesNotContain("Double-check the key")
            assertThat(message).contains("not the problem")
        }
        assertThat(LicensingMessages.LOCAL_VERIFICATION_REASON_CODES).contains("UNKNOWN_SIGNING_KEY")
        assertThat(LicensingMessages.LOCAL_VERIFICATION_REASON_CODES).contains("ASSERTION_VERIFICATION_FAILED")
    }

    @Test
    fun clock_advice_is_given_only_where_a_clock_can_actually_be_the_cause() {
        // 2026-09-04: one message served all twelve codes and told everyone to
        // check the date and time. On a real handset stranded on
        // UNKNOWN_SIGNING_KEY that advice was false, and it sent the
        // investigation to compare clocks that already matched exactly.
        for (code in LicensingMessages.CLOCK_FIXABLE_REASON_CODES) {
            assertThat(LicensingMessages.reasonMessage(code)).contains("date and time")
        }

        val notClockFixable =
            LicensingMessages.LOCAL_VERIFICATION_REASON_CODES - LicensingMessages.CLOCK_FIXABLE_REASON_CODES
        assertThat(notClockFixable).isNotEmpty()
        for (code in notClockFixable) {
            val message = LicensingMessages.reasonMessage(code)!!
            assertThat(message).doesNotContain("date and time")
            // The code is the fastest route to the cause and is also written
            // to licensing_events -- withholding it is what forced pulling a
            // database off the device to learn it.
            assertThat(message).contains(code)
        }

        // Every clock-fixable code must really be in the bucket it qualifies
        // the message for, or this test guards an empty intersection.
        assertThat(LicensingMessages.LOCAL_VERIFICATION_REASON_CODES)
            .containsAtLeastElementsIn(LicensingMessages.CLOCK_FIXABLE_REASON_CODES)
    }

    @Test
    fun a_declined_installation_is_never_reported_as_a_bad_key() {
        // Owner refuses to re-activate a terminal installation, so these are
        // what a staff DECLINE actually looks like from here. "Try the key
        // again" can only ever produce the same answer.
        for (code in listOf("INSTALLATION_DEACTIVATED", "INSTALLATION_REPLACED", "INSTALLATION_SUSPENDED")) {
            val message = LicensingMessages.reasonMessage(code)
            assertThat(message).isNotNull()
            assertThat(message).doesNotContain("Double-check the key")
        }
    }

    @Test
    fun an_unrecognised_code_falls_back_honestly_instead_of_blaming_the_key() {
        assertThat(LicensingMessages.reasonMessage("A_CODE_THIS_BUILD_HAS_NEVER_SEEN")).isNull()
        assertThat(LicensingMessages.UNKNOWN_REASON_TEMPLATE).contains("%s")
        assertThat(LicensingMessages.UNKNOWN_REASON_TEMPLATE).doesNotContain("Double-check the key")
    }

    @Test
    fun transient_codes_match_the_desktop_bucket() {
        // A held activation must survive every one of these untouched.
        assertThat(LicensingMessages.isTransient("NETWORK_UNAVAILABLE")).isTrue()
        assertThat(LicensingMessages.isTransient("RATE_LIMITED")).isTrue()
        // SIGNING_KEY_UNAVAILABLE (Owner's key service is down -- transient)
        // must not be confused with UNKNOWN_SIGNING_KEY (we cannot verify what
        // Owner signed -- a local verification failure).
        assertThat(LicensingMessages.isTransient("SIGNING_KEY_UNAVAILABLE")).isTrue()
        assertThat(LicensingMessages.isTransient("UNKNOWN_SIGNING_KEY")).isFalse()
        assertThat(LicensingMessages.isTransient("INSTALLATION_DEACTIVATED")).isFalse()
    }
}
