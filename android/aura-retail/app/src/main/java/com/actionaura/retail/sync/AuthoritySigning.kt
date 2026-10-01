package com.actionaura.retail.sync

import com.google.gson.JsonArray

/**
 * AUDIT-033 / S-AUTH (D3, Android): signs the AUTHORITY events of an outbox page.
 *
 * Python's embedded backend cannot sign on Android (the device key lives in the
 * Keystore, behind [com.actionaura.retail.licensing.DeviceIdentity]). For each
 * authority event the local `/_internal/outbox` route therefore sends
 * `payload._auth` WITHOUT `sig` plus a top-level `auth_tbs_b64`: the exact bytes
 * Python canonicalised. This function signs those bytes and puts the signature
 * into `payload._auth.sig`.
 *
 * Two rules that are easy to get wrong:
 *
 *  1. Sign the decoded bytes AS THEY ARE. Never re-canonicalise the payload here:
 *     Kotlin's JSON number handling is not Python's (int vs float spelling), so a
 *     re-rendered document would not match what the receiver rebuilds and the
 *     signature would never verify.
 *  2. Only the `sig` property is added. The page is a Gson tree, whose numbers
 *     keep their original source text, so `2` stays `2` and `2.0` stays `2.0`;
 *     no other payload value is touched.
 *
 * `auth_tbs_b64` is ALWAYS removed: it is a page-level hint, not part of the
 * event, and the relay would drop it anyway. A signing failure must never stop
 * the sync loop: the event is pushed without `sig` (every up-to-date receiver
 * parks it as unsigned and says so in sync review) and [onFailure] is told.
 *
 * Mutates [events] in place and returns how many events were signed.
 */
internal const val AUTH_TBS_KEY = "auth_tbs_b64"

internal fun signAuthorityEvents(
    events: JsonArray,
    sign: (ByteArray) -> String,
    onFailure: (Throwable) -> Unit = {},
): Int {
    var signed = 0
    for (element in events) {
        if (!element.isJsonObject) continue
        val event = element.asJsonObject
        val tbsElement = event.remove(AUTH_TBS_KEY) ?: continue
        try {
            val tbs = java.util.Base64.getDecoder().decode(tbsElement.asString)
            val auth = event.getAsJsonObject("payload").getAsJsonObject("_auth")
            auth.addProperty("sig", sign(tbs))
            signed++
        } catch (exc: Exception) {
            onFailure(exc)
        }
    }
    return signed
}
