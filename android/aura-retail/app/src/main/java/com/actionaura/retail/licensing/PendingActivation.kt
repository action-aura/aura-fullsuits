package com.actionaura.retail.licensing

import android.content.Context
import com.google.gson.Gson
import com.google.gson.JsonParser

/**
 * The "a key from this installation is already awaiting Owner's approval"
 * marker, and the rules that decide when it is still evidence.
 *
 * Android counterpart of the `aura.licensing.pendingActivation` localStorage
 * record that `products/retail/frontend/licensing.js` and
 * `app-shell.js::_markActivationPending` share. Same job, same shape, same
 * seven-day bound, for the same reason: without a marker, a device whose
 * activation Owner is holding for manual review keeps being shown a bare
 * license-key form (PENDING deliberately persists NO local state record --
 * see commercial_runtime/licensing_contracts/activation.py -- so GET
 * /api/licensing/status keeps answering a bare NOT_CONFIGURED), and the only
 * move available is to submit the same key again, collect another 202, and
 * be shown the form again. Forever.
 *
 * What is NOT stored here, ever, is the license key itself. It is credential
 * material -- the thing that proves entitlement to this product -- and the
 * backend goes out of its way to stop holding it (routes.py's activate()
 * nulls it in a `finally`). The key lives only in the activation screen's
 * in-memory Compose state, which is why the screen falls back to an honest
 * "enter the same key again" form after a process restart instead of
 * promising progress it cannot make. Same trade licensing.js's `pendingKey`
 * takes, and for the same reason.
 */
data class PendingActivationRecord(
    val version: Int,
    val atMillis: Long,
    val installationId: String?,
)

object PendingActivation {

    /**
     * A pending approval nobody ever ruled on is not evidence forever.
     * Without an expiry, a marker left behind by an install whose licensing
     * state was later wiped or re-provisioned would keep this screen claiming
     * "your key was received, waiting for approval" for a submission that no
     * longer exists on either side. Seven days is far past any real
     * manual-review turnaround, so ageing out here can only ever affect a
     * marker that is genuinely dead. Byte-identical to licensing.js's
     * PENDING_MAX_AGE_MS.
     */
    const val MAX_AGE_MS: Long = 7L * 24 * 60 * 60 * 1000

    /**
     * The record shape THIS build writes and understands. Stored as `v`, and
     * -- the part that was missing -- actually compared on the way back in.
     *
     * `v` was already parsed and already mandatory, which made it look like a
     * version check while being nothing of the kind: any integer passed. A
     * marker written by a future build with different semantics for the same
     * key would have been read as if it were this shape. That is exactly the
     * failure the field exists to prevent, and exactly what [decode]'s own
     * "not the record shape this build writes" promise claimed to cover.
     *
     * Bump this whenever the stored fields change meaning; an older or newer
     * marker is then discarded and the screen falls back to the key form,
     * which costs one extra ask and never a wrong screen.
     */
    const val CURRENT_VERSION: Int = 1

    /**
     * How often the awaiting-approval screen re-submits the held activation
     * to find out whether Owner has ruled on it yet. Each tick is a real
     * Owner round trip that does signature verification and DB writes on
     * their side, so it is deliberately slow -- it exists to make the "we
     * keep checking for you" promise TRUE, not to be instant. Same 30s
     * cadence as licensing.js's AWAITING_POLL_MS.
     */
    const val POLL_INTERVAL_MS: Long = 30_000L

    private val gson = Gson()

    fun encode(record: PendingActivationRecord): String = gson.toJson(
        mapOf(
            "v" to record.version,
            "at" to record.atMillis,
            "installation_id" to record.installationId,
        )
    )

    /**
     * Parses a stored marker, returning null when there is none, when its `v`
     * is not [CURRENT_VERSION], when the record is malformed, or when it has
     * aged out.
     *
     * Anything unparseable is discarded rather than trusted: it carries no
     * timestamp, so it could never be aged out, and an un-ageable marker is
     * precisely the stale-marker failure the timestamp exists to bound.
     * Falling back to the key form costs one extra ask; trusting it costs a
     * screen the user has no way to leave.
     */
    fun decode(raw: String?, nowMillis: Long): PendingActivationRecord? {
        if (raw.isNullOrBlank()) return null
        val obj = try {
            JsonParser.parseString(raw).takeIf { it.isJsonObject }?.asJsonObject
        } catch (_: Exception) { null } ?: return null

        val at = try {
            obj.get("at")?.takeUnless { it.isJsonNull }?.asLong
        } catch (_: Exception) { null } ?: return null
        val version = try {
            obj.get("v")?.takeUnless { it.isJsonNull }?.asInt
        } catch (_: Exception) { null } ?: return null
        // Parsing `v` and requiring it to be present is not a version check --
        // this is. Without the comparison any integer was accepted, so a
        // marker from a build with different semantics for these same keys
        // would have been read as if it were this shape.
        if (version != CURRENT_VERSION) return null
        val installationId = try {
            obj.get("installation_id")?.takeUnless { it.isJsonNull }?.asString
        } catch (_: Exception) { null }

        // Ageing out is the whole reason `at` is stored. A negative age (the
        // clock moved backwards since the marker was written) is treated as
        // fresh rather than expired: a rollback is not evidence the pending
        // review is over, and expiring on it would drop a live submission.
        if (nowMillis - at > MAX_AGE_MS) return null

        return PendingActivationRecord(version, at, installationId)
    }
}

/**
 * SharedPreferences-backed storage for the marker above. Deliberately thin:
 * every decision (shape validation, age-out) lives in [PendingActivation] so
 * it can be exercised under plain JUnit, where android.content.* is a
 * throwing stub.
 */
class PendingActivationStore(context: Context) {

    private val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun read(nowMillis: Long = System.currentTimeMillis()): PendingActivationRecord? {
        val record = PendingActivation.decode(prefs.getString(KEY, null), nowMillis)
        // A marker that failed to decode (or aged out) is actively removed
        // rather than left to be re-read and re-rejected on every render.
        if (record == null) clear()
        return record
    }

    fun mark(installationId: String?, nowMillis: Long = System.currentTimeMillis()) {
        val record = PendingActivationRecord(
            version = PendingActivation.CURRENT_VERSION, atMillis = nowMillis, installationId = installationId
        )
        prefs.edit().putString(KEY, PendingActivation.encode(record)).apply()
    }

    fun clear() {
        prefs.edit().remove(KEY).apply()
    }

    private companion object {
        // Same SharedPreferences file the rest of the app already uses
        // (see ui/i18n/Strings.kt's AppLocale) -- one more key, not a
        // second preferences file.
        const val PREFS = "aura_prefs"
        const val KEY = "licensing_pending_activation"
    }
}
