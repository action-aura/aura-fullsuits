package com.actionaura.clinic.licensing

import java.text.Normalizer

/**
 * Kotlin port of commercial_runtime/licensing_contracts/canonical.py, which
 * is itself a byte-for-byte port of owner/app/licensing_service/canonical.py
 * (Phase 6, ADR-6.4). This is the single most security-critical piece of new
 * Android code in Phase 7: every device-signed request's signature covers
 * EXACTLY these bytes, and if this Kotlin implementation ever disagrees with
 * Owner's Python canonicalization for the same logical payload, every
 * signature Android produces silently fails verification. Cross-verified
 * directly against real Python output, not just against this file's own
 * round-trip tests -- see CanonicalCrossVerifyTest.kt.
 *
 * Deliberately hand-rolled rather than using Gson's default serialization:
 * Gson does not sort keys, does not NFC-normalize strings, and its
 * whitespace/escaping choices are not guaranteed to match Python's
 * json.dumps(..., ensure_ascii=False, separators=(",", ":")) byte-for-byte.
 *
 * Rules (must stay in lockstep with canonical.py):
 * - Object keys sorted lexicographically (by UTF-16 code unit, matching
 *   Python's default string sort for the ASCII-range field names this
 *   protocol actually uses -- see the doc comment on sortedKeysMatchPython
 *   in CanonicalCrossVerifyTest.kt for why this is safe here).
 * - Every string value NFC-normalized.
 * - Compact separators, no insignificant whitespace.
 * - Arrays preserve given order (semantically ordered, unlike object keys).
 * - null is preserved as JSON null, never stripped.
 * - Non-finite numbers (NaN/Infinity) rejected.
 * - Maximum nesting depth 16.
 */

class CanonicalizationError(message: String) : Exception(message)

private const val MAX_CANONICAL_DEPTH = 16

fun canonicalize(payload: Map<String, Any?>): String {
    val normalized = normalizeValue(payload, 0)
    val builder = StringBuilder()
    serializeValue(normalized, builder)
    return builder.toString()
}

fun canonicalizeBytes(payload: Map<String, Any?>): ByteArray =
    canonicalize(payload).toByteArray(Charsets.UTF_8)

private fun normalizeValue(value: Any?, depth: Int): Any? {
    if (depth > MAX_CANONICAL_DEPTH) {
        throw CanonicalizationError("Payload nesting exceeds maximum allowed depth.")
    }
    return when (value) {
        null -> null
        is String -> Normalizer.normalize(value, Normalizer.Form.NFC)
        is Map<*, *> -> {
            val out = LinkedHashMap<String, Any?>()
            for ((k, v) in value) {
                out[k.toString()] = normalizeValue(v, depth + 1)
            }
            out
        }
        is List<*> -> value.map { normalizeValue(it, depth + 1) }
        is Double -> {
            if (value.isNaN() || value.isInfinite()) {
                throw CanonicalizationError("Non-finite numeric value is not permitted in a canonical payload.")
            }
            value
        }
        is Float -> normalizeValue(value.toDouble(), depth)
        is Boolean, is Int, is Long -> value
        else -> throw CanonicalizationError("Unsupported value type in canonical payload: ${value::class}")
    }
}

private fun serializeValue(value: Any?, out: StringBuilder) {
    when (value) {
        null -> out.append("null")
        is Boolean -> out.append(value.toString())
        is Int -> out.append(value.toString())
        is Long -> out.append(value.toString())
        is Double -> out.append(formatDouble(value))
        is String -> serializeString(value, out)
        is Map<*, *> -> {
            out.append('{')
            val sortedKeys = value.keys.map { it as String }.sorted()
            for ((i, key) in sortedKeys.withIndex()) {
                if (i > 0) out.append(',')
                serializeString(key, out)
                out.append(':')
                serializeValue(value[key], out)
            }
            out.append('}')
        }
        is List<*> -> {
            out.append('[')
            for ((i, item) in value.withIndex()) {
                if (i > 0) out.append(',')
                serializeValue(item, out)
            }
            out.append(']')
        }
        else -> throw CanonicalizationError("Unsupported value type during serialization: ${value?.let { it::class }}")
    }
}

private fun formatDouble(value: Double): String {
    // Only reachable for entitlement/offline-policy numeric fields that
    // happen to be floats -- the activation/check-in/deactivation request
    // bodies this client actually signs are ints/strings/bools/null only
    // (see client field lists), so this path is not exercised by any
    // currently-signed request, but is implemented correctly regardless.
    return if (value == Math.floor(value) && !value.isInfinite()) {
        value.toLong().toString()
    } else {
        value.toString()
    }
}

private fun serializeString(value: String, out: StringBuilder) {
    // Matches Python's json.dumps(..., ensure_ascii=False) escaping exactly:
    // only '"', '\\', and control characters (< 0x20) are escaped; every
    // other Unicode code point (including non-ASCII) is emitted as literal
    // UTF-8, not \uXXXX-escaped.
    out.append('"')
    for (ch in value) {
        when (ch) {
            '"' -> out.append("\\\"")
            '\\' -> out.append("\\\\")
            '\n' -> out.append("\\n")
            '\r' -> out.append("\\r")
            '\t' -> out.append("\\t")
            else -> {
                if (ch.code < 0x20) {
                    out.append(String.format("\\u%04x", ch.code))
                } else {
                    out.append(ch)
                }
            }
        }
    }
    out.append('"')
}
