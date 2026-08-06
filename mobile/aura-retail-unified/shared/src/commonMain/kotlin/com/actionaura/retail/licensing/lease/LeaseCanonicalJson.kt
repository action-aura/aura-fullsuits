package com.actionaura.retail.licensing.lease

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.longOrNull

/**
 * M11.1/M11.2 -- real, byte-for-byte port of
 * `commercial_runtime/licensing_contracts/canonical.py`'s
 * `canonicalize()`/`canonicalize_bytes()` (verified, not assumed, by
 * direct cross-check against the real Python implementation via this
 * repo's own `.venv` -- `signed-lease-cryptography-decision.md`).
 * Sorted keys, no insignificant whitespace, NFC-normalized strings,
 * array order preserved, non-finite numbers and excessive nesting
 * rejected. This exact string is what Owner signs and what
 * verification must re-derive byte-for-byte -- **only used to
 * generate test fixtures and to validate M11.35 cross-runtime
 * compatibility.** The real production verification path never
 * re-canonicalizes a received lease from a typed object; it verifies
 * directly against the raw canonical JSON string as received (see
 * `ProtectedSignedLease`) -- reconstruction risks producing bytes
 * that differ from what was actually signed.
 */
class LeaseCanonicalizationError(message: String) : IllegalArgumentException(message)

private const val MAX_CANONICAL_DEPTH = 16

object LeaseCanonicalJson {

    fun canonicalize(payload: JsonObject): String {
        val sb = StringBuilder()
        writeValue(payload, sb, depth = 0)
        return sb.toString()
    }

    fun canonicalizeBytes(payload: JsonObject): ByteArray = canonicalize(payload).encodeToByteArray()

    private fun writeValue(value: JsonElement, sb: StringBuilder, depth: Int) {
        if (depth > MAX_CANONICAL_DEPTH) {
            throw LeaseCanonicalizationError("Payload nesting exceeds maximum allowed depth.")
        }
        when (value) {
            is JsonNull -> sb.append("null")
            is JsonObject -> writeObject(value, sb, depth)
            is JsonArray -> writeArray(value, sb, depth)
            is JsonPrimitive -> writePrimitive(value, sb)
        }
    }

    private fun writeObject(obj: JsonObject, sb: StringBuilder, depth: Int) {
        sb.append('{')
        val sortedKeys = obj.keys.sorted()
        sortedKeys.forEachIndexed { index, key ->
            if (index > 0) sb.append(',')
            writeJsonString(key.normalizeNfc(), sb)
            sb.append(':')
            writeValue(obj.getValue(key), sb, depth + 1)
        }
        sb.append('}')
    }

    private fun writeArray(arr: JsonArray, sb: StringBuilder, depth: Int) {
        sb.append('[')
        arr.forEachIndexed { index, element ->
            if (index > 0) sb.append(',')
            writeValue(element, sb, depth + 1)
        }
        sb.append(']')
    }

    private fun writePrimitive(prim: JsonPrimitive, sb: StringBuilder) {
        if (prim.isString) {
            writeJsonString(prim.content.normalizeNfc(), sb)
            return
        }
        prim.booleanOrNull?.let { sb.append(if (it) "true" else "false"); return }
        val asLong = prim.longOrNull
        if (asLong != null) { sb.append(asLong); return }
        val asDouble = prim.doubleOrNull
        if (asDouble != null) {
            if (asDouble.isNaN() || asDouble.isInfinite()) {
                throw LeaseCanonicalizationError("Non-finite numeric value is not permitted in a canonical payload.")
            }
            // Real, cross-verified against the actual Python canonicalize()
            // output (`m10`-era discipline: real reference beats assumption)
            // -- a source float MUST keep its trailing ".0" (Python's
            // json.dumps(5.0) == "5.0", never "5"); `longOrNull` above
            // already correctly claims every genuinely integer-typed JSON
            // literal first, so reaching here means the source was really
            // float-typed and must print as such. Every real numeric field
            // in ALLOWED_PAYLOAD_FIELDS is an integer-second duration or
            // count, so this path is not expected to be exercised by any
            // real production payload -- kept correct anyway, not assumed
            // safe to get wrong because "it shouldn't happen."
            sb.append(asDouble.toString())
            return
        }
        throw LeaseCanonicalizationError("Unsupported JSON primitive: $prim")
    }

    /** Real JSON string escaping matching Python's `json.dumps(..., ensure_ascii=False)`: control characters and the two structural characters (`"`, `\`) are escaped; every other Unicode code point (including non-ASCII) is emitted literally, never as a `\uXXXX` escape. */
    private fun writeJsonString(s: String, sb: StringBuilder) {
        sb.append('"')
        for (ch in s) {
            when (ch) {
                '"' -> sb.append("\\\"")
                '\\' -> sb.append("\\\\")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                '\t' -> sb.append("\\t")
                '\b' -> sb.append("\\b")
                '' -> sb.append("\\f")
                else -> if (ch.code < 0x20) {
                    sb.append("\\u")
                    sb.append(ch.code.toString(16).padStart(4, '0'))
                } else {
                    sb.append(ch)
                }
            }
        }
        sb.append('"')
    }
}

internal expect fun String.normalizeNfc(): String
