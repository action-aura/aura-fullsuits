package com.actionaura.retail.sync

import android.content.Context
import com.google.gson.JsonParser

/**
 * Per-device settings for the LAN site relay hub this device is paired to.
 * Same `"aura_prefs"` SharedPreferences file and the same load/set shape as
 * [com.actionaura.retail.printer.PrinterPrefs] -- see that class's doc
 * comment for why this shape (plain `object`, no cached/observed Compose
 * state, a fresh `getSharedPreferences` read per call) is deliberate here
 * too: these four values are read at pairing time and at sync-connection
 * time, never on every composition, so there is no correctness reason to
 * cache them and every reason not to risk a stale cached read.
 *
 * Default empty, per this codebase's "a new feature must cost an install
 * that never uses it nothing" rule: no hub configured means this device
 * behaves exactly as it does today (talking only to Owner's cloud relay, if
 * any), and no sync/pairing UI needs to change behavior for an install that
 * never pairs to a LAN hub.
 */
object HubPrefs {
    private const val PREFS = "aura_prefs"
    private const val KEY_BASE_URL = "hub_base_url"
    private const val KEY_SPKI_PIN = "hub_spki_pin"
    private const val KEY_DEVICE_PUBLIC_KEY = "hub_device_public_key"
    private const val KEY_INSTALLATION_ID = "hub_installation_id"

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun getBaseUrl(ctx: Context): String = prefs(ctx).getString(KEY_BASE_URL, "") ?: ""
    fun getSpkiPin(ctx: Context): String = prefs(ctx).getString(KEY_SPKI_PIN, "") ?: ""

    /**
     * The hub's Ed25519 DEVICE signing key -- NOT the TLS SPKI pin stored
     * alongside it in [getSpkiPin]. These two base64 strings look like an
     * accidental duplication of the same fact sitting next to each other
     * and are not: the SPKI pin authenticates the TLS CONNECTION this
     * device opens to the hub (see [SpkiPinning]'s class doc), while this
     * device public key is what lets this device verify the AUTHENTICITY of
     * the hub's separately-signed UDP addressing beacon (see
     * `pairing_payload`'s doc comment in
     * `commercial_runtime/sync/site_relay/pairing.py` for the full
     * reasoning) -- a connectionless UDP datagram, not the TLS handshake at
     * all. Do not "deduplicate" these two fields; they secure two entirely
     * different channels against two entirely different threats, and they
     * come from two entirely different keys on the hub.
     */
    fun getHubDevicePublicKey(ctx: Context): String = prefs(ctx).getString(KEY_DEVICE_PUBLIC_KEY, "") ?: ""
    fun getHubInstallationId(ctx: Context): String = prefs(ctx).getString(KEY_INSTALLATION_ID, "") ?: ""

    /**
     * Stores everything a device learns when it pairs with a hub, in one
     * write. [baseUrl] is trimmed and stored WITHOUT a trailing slash --
     * [SyncRelayClient] (and this codebase's other relay clients) always
     * appends a leading-slash path (`/api/sync/v1/...`) itself, so a stored
     * trailing slash would silently produce a double-slash URL.
     */
    fun set(
        ctx: Context,
        baseUrl: String,
        spkiPin: String,
        hubDevicePublicKey: String,
        hubInstallationId: String,
    ) {
        prefs(ctx).edit()
            .putString(KEY_BASE_URL, baseUrl.trim().trimEnd('/'))
            .putString(KEY_SPKI_PIN, spkiPin.trim())
            .putString(KEY_DEVICE_PUBLIC_KEY, hubDevicePublicKey.trim())
            .putString(KEY_INSTALLATION_ID, hubInstallationId.trim())
            .apply()
    }

    /** Forgets this device's hub pairing entirely -- e.g. re-pairing to a
     * different hub, or leaving LAN sync altogether. */
    fun clear(ctx: Context) {
        prefs(ctx).edit()
            .remove(KEY_BASE_URL)
            .remove(KEY_SPKI_PIN)
            .remove(KEY_DEVICE_PUBLIC_KEY)
            .remove(KEY_INSTALLATION_ID)
            .apply()
    }

    /**
     * True only once BOTH the URL and the pin are set. Deliberately not
     * "URL alone is enough": a base URL with no pin would name a hub this
     * device has no way to authenticate, and treating that as "configured"
     * would invite a later code path to open an unpinned connection to it
     * (exactly the failure [SpkiPinning]'s class doc warns against) --
     * pairing is all-or-nothing, never a URL saved now and a pin trusted
     * later.
     */
    fun isConfigured(ctx: Context): Boolean = getBaseUrl(ctx).isNotBlank() && getSpkiPin(ctx).isNotBlank()
}

/**
 * The hub's LAN pairing QR payload, once parsed -- mirrors
 * `pairing_payload()`'s five fields in
 * `commercial_runtime/sync/site_relay/pairing.py` exactly (verified against
 * that function's actual `return` dict, not assumed): `base_url`,
 * `spki_pin`, `hub_installation_id`, `hub_device_public_key`,
 * `pairing_code`.
 */
data class PairingPayload(
    val baseUrl: String,
    val spkiPin: String,
    val hubDevicePublicKey: String,
    val hubInstallationId: String,
    val pairingCode: String,
)

/**
 * Parses the hub's pairing QR JSON payload into a [PairingPayload], or
 * throws [IllegalArgumentException] naming exactly what was wrong -- never
 * lets a raw JSON-parser exception escape to the caller.
 *
 * Uses Gson, matching [SyncRelayClient]'s own idiom exactly: `JsonParser
 * .parseString(...)` to get a root element, then `element.get(key)
 * ?.takeUnless { it.isJsonNull }?.asString` to read each field, and a broad
 * `catch (exc: Exception)` around the whole parse -- see
 * `SyncRelayClient.requestJson`'s identical `JsonParser.parseString(outcome
 * .body).asJsonObject` call, wrapped the exact same way, for the precedent
 * this follows. Gson is already a real (non-stubbed) dependency of this
 * exact package, so the pairing payload -- which arrives from the same kind
 * of untrusted network/QR source as everything else [SyncRelayClient]
 * parses -- is read by the one JSON library this module already depends on
 * and already trusts, rather than by a second, hand-written parser next to
 * it. (`org.json.JSONObject` was tried first, per this task's original
 * plan, and does not work here: it ships as part of the Android SDK's
 * compile-time-only stub jar, and throws `RuntimeException("... not
 * mocked ...")` the instant it is actually invoked under this module's
 * plain JUnit tests, which have no Robolectric shadow registered -- the
 * same reason `app/build.gradle`'s own dependency comment gives for why
 * [SyncRelayClient] itself uses Gson instead of `org.json`. A hand-rolled
 * parser was tried next as a fallback and replaced with this Gson-based
 * version once it became clear Gson -- already on the classpath, already
 * trusted -- was the correct fallback all along, not a bespoke parser that
 * would only ever be exercised by this one function and would drift from
 * real JSON at some edge Gson already handles correctly.)
 */
fun parsePairingPayload(json: String): PairingPayload {
    try {
        val element = JsonParser.parseString(json)
        if (!element.isJsonObject) {
            throw IllegalArgumentException("Pairing payload is not a JSON object.")
        }
        val root = element.asJsonObject

        fun required(key: String): String {
            val value = root.get(key)?.takeUnless { it.isJsonNull }?.asString
            if (value.isNullOrBlank()) {
                throw IllegalArgumentException("Pairing payload is missing required field \"$key\".")
            }
            return value
        }

        val baseUrl = required("base_url")
        // A payload naming an http:// hub would send every device-signed sync
        // request body -- and the entire pulled event stream -- in the clear.
        // See SpkiPinning's class doc for why TLS (and therefore https://) is
        // load-bearing here, not optional.
        if (!baseUrl.startsWith("https://")) {
            throw IllegalArgumentException(
                "Pairing payload's base_url must start with \"https://\" (got \"$baseUrl\") -- an " +
                    "http:// hub would send device-signed sync data, and the whole pulled event " +
                    "stream, in the clear."
            )
        }

        return PairingPayload(
            baseUrl = baseUrl,
            spkiPin = required("spki_pin"),
            hubDevicePublicKey = required("hub_device_public_key"),
            hubInstallationId = required("hub_installation_id"),
            pairingCode = required("pairing_code"),
        )
    } catch (fieldError: IllegalArgumentException) {
        // Already the exact, field-naming exception this function promises
        // to throw -- pass it through unchanged rather than re-wrapping it
        // below and losing which field was named.
        throw fieldError
    } catch (malformed: Exception) {
        // Gson throws several different runtime exception types for
        // different flavors of malformed input (JsonSyntaxException for
        // unparsable text, IllegalStateException for e.g. calling
        // `.asString` on a JSON object/array value) -- none of them may ever
        // escape to the caller as themselves; test 11's whole point is that
        // this always comes back as IllegalArgumentException.
        throw IllegalArgumentException("Pairing payload is not valid JSON: ${malformed.message}", malformed)
    }
}
