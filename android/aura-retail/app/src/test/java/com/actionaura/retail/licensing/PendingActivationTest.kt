package com.actionaura.retail.licensing

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * The pending-activation marker's shape and expiry rules. Pure (no
 * SharedPreferences) on purpose -- android.content.* is a throwing stub under
 * plain JUnit, so every decision worth testing lives in [PendingActivation]
 * and only the I/O lives in PendingActivationStore.
 */
class PendingActivationTest {

    private val now = 1_700_000_000_000L

    @Test
    fun a_fresh_marker_round_trips() {
        val encoded = PendingActivation.encode(PendingActivationRecord(1, now, "inst-42"))
        val decoded = PendingActivation.decode(encoded, now + 1_000)
        assertThat(decoded).isEqualTo(PendingActivationRecord(1, now, "inst-42"))
    }

    @Test
    fun a_marker_without_an_installation_id_is_still_valid() {
        // The 202 body always carries installation_id today, but a marker
        // written without one must still hold the screen open -- its job is
        // "a key is already awaiting approval", not "here is the id".
        val encoded = PendingActivation.encode(PendingActivationRecord(1, now, null))
        assertThat(PendingActivation.decode(encoded, now)?.installationId).isNull()
    }

    @Test
    fun a_marker_older_than_seven_days_is_no_longer_evidence() {
        // A marker left behind by an install whose licensing state was later
        // wiped would otherwise claim "waiting for approval" forever, for a
        // submission that no longer exists on either side.
        val encoded = PendingActivation.encode(PendingActivationRecord(1, now, "inst-42"))
        val justInside = now + PendingActivation.MAX_AGE_MS - 1
        val justOutside = now + PendingActivation.MAX_AGE_MS + 1
        assertThat(PendingActivation.decode(encoded, justInside)).isNotNull()
        assertThat(PendingActivation.decode(encoded, justOutside)).isNull()
    }

    @Test
    fun anything_that_is_not_this_records_shape_is_discarded() {
        // Notably a bare "1" from an earlier build: it carries no timestamp,
        // so it could never age out, and an un-ageable marker is precisely
        // the stale-marker failure the timestamp exists to bound.
        assertThat(PendingActivation.decode("1", now)).isNull()
        assertThat(PendingActivation.decode("not json at all", now)).isNull()
        assertThat(PendingActivation.decode("{\"v\":1}", now)).isNull()   // no `at`
        assertThat(PendingActivation.decode("", now)).isNull()
        assertThat(PendingActivation.decode(null, now)).isNull()
    }

    @Test
    fun the_marker_never_carries_the_license_key() {
        // The key is credential material; the backend goes out of its way to
        // stop holding it (routes.py's activate() nulls it in a `finally`).
        // A marker that wrote it to disk, where it outlives the process,
        // would quietly undo all of that.
        val encoded = PendingActivation.encode(PendingActivationRecord(1, now, "inst-42"))
        assertThat(encoded.lowercase()).doesNotContain("license_key")
        assertThat(encoded.lowercase()).doesNotContain("licensekey")
    }

    @Test
    fun the_poll_cadence_matches_the_desktop_awaiting_screen() {
        assertThat(PendingActivation.POLL_INTERVAL_MS).isEqualTo(30_000L)
        assertThat(PendingActivation.MAX_AGE_MS).isEqualTo(7L * 24 * 60 * 60 * 1000)
    }
}
