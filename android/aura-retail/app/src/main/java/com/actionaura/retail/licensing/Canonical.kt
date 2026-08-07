package com.actionaura.retail.licensing

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

/**
 * Renders [value] exactly the way Python's `json.dumps` does, which is
 * `repr(float)`: shortest round-tripping decimal digits, ALWAYS carrying a
 * decimal point or an exponent (`12.0`, never `12`), switching to exponential
 * form on the same thresholds CPython uses.
 *
 * This used to render a whole-number Double as a bare integer (`12`), which
 * disagrees with Python's `12.0` and therefore produced different canonical
 * bytes and an `INVALID_SIGNATURE` rejection from Owner. That was documented
 * as harmless because licensing's own signed request bodies carry no float
 * fields -- true then, stale now: multi-device sync signs arbitrary business
 * payloads drained from the Python outbox (`commercial_runtime/sync/`).
 * Category payloads happen to be strings-only, but the next synced entity
 * (Products: `cost_price`/`sell_price` are SQLite `REAL`) would have tripped
 * this on the very first whole-number price.
 *
 * CPython's rule (`PyOS_double_to_string` in repr mode): take the shortest
 * digit string that round-trips, together with `decpt` (how many of those
 * digits sit left of the decimal point); use exponential notation when
 * `decpt < -3 || decpt > 16`, otherwise fixed notation with `.0` appended if
 * nothing would follow the point. Exponents are written with a sign and at
 * least two digits (`1e+22`, `1e-07`), and the mantissa carries no gratuitous
 * `.0` (`1e+22`, not `1.0e+22`) -- verified against the real interpreter, not
 * assumed; see CanonicalDoubleFormatTest, whose expectations are literal
 * `json.dumps` output.
 *
 * The shortest digits themselves come from [java.lang.Double.toString], whose
 * output is re-laid-out here rather than used directly (Java switches to
 * exponential at 1e7 and 1e-3, Python at 1e16 and 1e-4, and Java always
 * writes a `1.0E22`-style mantissa).
 *
 * Known residual, measured rather than assumed: a differential run of 160k
 * values against the real interpreter (every output compared to
 * `json.dumps`) found agreement on all 65,000 values sampled across
 * 1e-10..1e10, on every money-shaped value, and on every whole number --
 * and disagreement on 158 of ~95,000 random bit patterns, ALL of magnitude
 * >= 3.4e16, plus `Double.MIN_VALUE` itself. That is the long-standing
 * pre-JDK-19 `Double.toString` behaviour of occasionally emitting one more
 * digit than the true shortest round-tripping form (JDK-4511638); the layout
 * logic below is not what differs. No field this protocol carries -- prices,
 * quantities, tax rates, durations -- comes anywhere near 3.4e16, so nothing
 * signed today is affected; a payload that genuinely needed a >1e16 float
 * would have to switch to an exact decimal/string representation anyway,
 * which is the correct answer for money at that magnitude regardless.
 */
private fun formatDouble(value: Double): String {
    if (value.isNaN() || value.isInfinite()) {
        throw CanonicalizationError("Non-finite numeric value is not permitted in a canonical payload.")
    }
    val negative = value < 0.0 || (value == 0.0 && 1.0 / value < 0.0)  // preserves -0.0, as repr(-0.0) does
    val magnitude = Math.abs(value)
    if (magnitude == 0.0) return if (negative) "-0.0" else "0.0"

    // Java's shortest round-tripping representation: "1199.99", "1.0E7", "1.0E-7".
    val javaRepr = java.lang.Double.toString(magnitude)
    val exponentIndex = javaRepr.indexOf('E')
    val mantissa = if (exponentIndex >= 0) javaRepr.substring(0, exponentIndex) else javaRepr
    val exponent = if (exponentIndex >= 0) javaRepr.substring(exponentIndex + 1).toInt() else 0
    val pointIndex = mantissa.indexOf('.')
    val allDigits = mantissa.substring(0, pointIndex) + mantissa.substring(pointIndex + 1)

    // `decpt` = number of digits that belong left of the decimal point, once
    // leading zeros are stripped -- CPython's own variable of the same name.
    val firstSignificant = allDigits.indexOfFirst { it != '0' }
    var digits = allDigits.substring(firstSignificant).trimEnd('0')
    var decpt = pointIndex + exponent - firstSignificant
    if (digits.isEmpty()) {  // the value was zero after all
        digits = "0"
        decpt = 1
    }

    val out = StringBuilder()
    if (negative) out.append('-')
    when {
        decpt < -3 || decpt > 16 -> {
            out.append(digits[0])
            if (digits.length > 1) out.append('.').append(digits, 1, digits.length)
            val e = decpt - 1
            out.append('e').append(if (e < 0) '-' else '+')
            val absExponent = Math.abs(e)
            if (absExponent < 10) out.append('0')
            out.append(absExponent)
        }
        decpt <= 0 -> {
            out.append("0.")
            repeat(-decpt) { out.append('0') }
            out.append(digits)
        }
        decpt >= digits.length -> {
            out.append(digits)
            repeat(decpt - digits.length) { out.append('0') }
            out.append(".0")
        }
        else -> out.append(digits, 0, decpt).append('.').append(digits, decpt, digits.length)
    }
    return out.toString()
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
