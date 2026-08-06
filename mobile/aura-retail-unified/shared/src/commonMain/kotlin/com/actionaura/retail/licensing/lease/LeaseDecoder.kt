package com.actionaura.retail.licensing.lease

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject

/**
 * M11.4 -- real, bounded, fail-closed decoding of a [ProtectedSignedLease]
 * into a validated [JsonObject] payload, ready for signature
 * verification. Every limit below is a real, deliberate, generous
 * ceiling (matches real production lease sizes -- 27 allowlisted
 * fields, none large -- by a wide margin) chosen to reject pathological
 * input, never to constrain a legitimate lease.
 */
object LeaseDecodingLimits {
    const val MAX_PAYLOAD_JSON_CHARS = 32_768
    const val MAX_SIGNATURE_B64_CHARS = 256
    const val MAX_KEY_IDENTIFIER_CHARS = 256
    const val MAX_ALGORITHM_CHARS = 64
    const val MAX_STRING_FIELD_CHARS = 4_096
    const val MAX_COLLECTION_ENTRIES = 256
    const val MAX_NESTING_DEPTH = 16
    const val ED25519_PUBLIC_KEY_BYTES = 32
    const val ED25519_SIGNATURE_BYTES = 64
}

private val decodingJson = Json { ignoreUnknownKeys = false; isLenient = false }

sealed interface LeaseDecodeResult {
    data class Success(val payload: JsonObject) : LeaseDecodeResult
    data class Failure(val failure: LeaseVerificationFailure) : LeaseDecodeResult
}

object LeaseDecoder {

    fun decode(lease: ProtectedSignedLease): LeaseDecodeResult {
        if (lease.payloadCanonicalJson.isEmpty()) {
            return fail(LeaseFailureCode.MALFORMED_ENVELOPE, "empty payload")
        }
        if (lease.payloadCanonicalJson.length > LeaseDecodingLimits.MAX_PAYLOAD_JSON_CHARS) {
            return fail(LeaseFailureCode.OVERSIZED_PAYLOAD)
        }
        if (lease.signature.length > LeaseDecodingLimits.MAX_SIGNATURE_B64_CHARS) {
            return fail(LeaseFailureCode.OVERSIZED_SIGNATURE)
        }
        if (lease.signature.isEmpty()) {
            return fail(LeaseFailureCode.MALFORMED_SIGNATURE, "empty signature")
        }
        if (lease.signingKeyId.isEmpty() || lease.signingKeyId.length > LeaseDecodingLimits.MAX_KEY_IDENTIFIER_CHARS) {
            return fail(LeaseFailureCode.OVERSIZED_KEY_IDENTIFIER)
        }
        if (lease.algorithm.isEmpty() || lease.algorithm.length > LeaseDecodingLimits.MAX_ALGORITHM_CHARS) {
            return fail(LeaseFailureCode.MALFORMED_ENVELOPE, "malformed algorithm field")
        }

        if (hasDuplicateTopLevelKeys(lease.payloadCanonicalJson)) {
            return fail(LeaseFailureCode.DUPLICATE_FIELD)
        }

        val element: JsonElement = try {
            decodingJson.parseToJsonElement(lease.payloadCanonicalJson)
        } catch (e: Exception) {
            return fail(LeaseFailureCode.MALFORMED_ENVELOPE, e::class.simpleName)
        }
        val obj = try {
            element.jsonObject
        } catch (e: Exception) {
            return fail(LeaseFailureCode.MALFORMED_ENVELOPE, "payload is not a JSON object")
        }

        boundsCheck(obj, depth = 0)?.let { return LeaseDecodeResult.Failure(it) }

        for (key in obj.keys) {
            if (key !in LeasePayloadFields.ALLOWED) {
                return fail(LeaseFailureCode.UNKNOWN_REQUIRED_FIELD, "unexpected field: $key")
            }
        }
        for (field in LeasePayloadFields.REQUIRED) {
            if (field !in obj) return fail(LeaseFailureCode.MALFORMED_ENVELOPE, "missing required field: $field")
        }

        val flatLower = obj.toString().lowercase()
        for (marker in LeasePayloadFields.FORBIDDEN_MARKERS) {
            if (marker in flatLower) return fail(LeaseFailureCode.FORBIDDEN_FIELD, "forbidden marker present")
        }

        return LeaseDecodeResult.Success(obj)
    }

    private fun boundsCheck(value: JsonElement, depth: Int): LeaseVerificationFailure? {
        if (depth > LeaseDecodingLimits.MAX_NESTING_DEPTH) {
            return LeaseVerificationFailure(LeaseFailureCode.EXCESSIVE_NESTING)
        }
        when (value) {
            is JsonObject -> {
                if (value.size > LeaseDecodingLimits.MAX_COLLECTION_ENTRIES) {
                    return LeaseVerificationFailure(LeaseFailureCode.OVERSIZED_COLLECTION)
                }
                for ((_, v) in value) {
                    boundsCheck(v, depth + 1)?.let { return it }
                }
            }
            is kotlinx.serialization.json.JsonArray -> {
                if (value.size > LeaseDecodingLimits.MAX_COLLECTION_ENTRIES) {
                    return LeaseVerificationFailure(LeaseFailureCode.OVERSIZED_COLLECTION)
                }
                for (v in value) {
                    boundsCheck(v, depth + 1)?.let { return it }
                }
            }
            is JsonPrimitive -> {
                if (value.isString && value.content.length > LeaseDecodingLimits.MAX_STRING_FIELD_CHARS) {
                    return LeaseVerificationFailure(LeaseFailureCode.OVERSIZED_STRING_FIELD)
                }
            }
            else -> Unit
        }
        return null
    }

    /**
     * Real, deliberately scoped duplicate-key detector: a lightweight
     * tokenizer that tracks brace/bracket depth and flags a repeated
     * quoted key at the SAME nesting level -- catches the real attack
     * this guards against (`{"expires_at":"...","expires_at":"..."}`,
     * silently resolved to the last value by a naive parser) without
     * implementing a full alternate JSON parser. Legitimate same-named
     * keys at DIFFERENT levels (e.g. a key named the same as a field
     * inside a nested `entitlements` object) are correctly not flagged.
     */
    private fun hasDuplicateTopLevelKeys(json: String): Boolean {
        var depth = 0
        var i = 0
        val seenAtDepth = mutableMapOf<Int, MutableSet<String>>()
        while (i < json.length) {
            val c = json[i]
            when (c) {
                '{', '[' -> { depth++; i++ }
                '}', ']' -> { seenAtDepth.remove(depth); depth--; i++ }
                '"' -> {
                    val (key, next) = readJsonString(json, i) ?: return false
                    i = next
                    // A key is only what's immediately followed by ':' (skipping whitespace) at object level.
                    var j = i
                    while (j < json.length && json[j].isWhitespace()) j++
                    if (j < json.length && json[j] == ':') {
                        val set = seenAtDepth.getOrPut(depth) { mutableSetOf() }
                        if (!set.add(key)) return true
                    }
                }
                else -> i++
            }
        }
        return false
    }

    private fun readJsonString(s: String, startQuote: Int): Pair<String, Int>? {
        val sb = StringBuilder()
        var i = startQuote + 1
        while (i < s.length) {
            when (val c = s[i]) {
                '"' -> return sb.toString() to (i + 1)
                '\\' -> { if (i + 1 >= s.length) return null; sb.append(c).append(s[i + 1]); i += 2 }
                else -> { sb.append(c); i++ }
            }
        }
        return null
    }

    private fun fail(code: LeaseFailureCode, reason: String? = null): LeaseDecodeResult.Failure =
        LeaseDecodeResult.Failure(LeaseVerificationFailure(code, reason))
}

/** Real, exact port of `assertion_verifier.py`'s `ALLOWED_PAYLOAD_FIELDS`/`FORBIDDEN_ASSERTION_MARKERS` (`canonical-signed-lease-authority-audit.md`). */
object LeasePayloadFields {
    val ALLOWED: Set<String> = setOf(
        "assertion_id", "issuer", "product_code", "license_public_id", "installation_public_id",
        "platform", "app_version_policy", "release_channel", "issued_at", "not_before", "expires_at",
        "license_status", "installation_status", "subscription_status", "allowed_device_count",
        "device_key_fingerprint", "entitlements", "offline_policy", "contract_version",
        "commercial_policy_version", "renewal_status", "plan_code", "term_start", "term_end",
        "past_due_since", "commercial_grace_end", "pilot_status", "emergency_extension_id",
    )

    /** Real, deliberately narrower than [ALLOWED] -- only the fields the M11 verifier's own pipeline structurally requires to reach a decision (matches what `assertion_verifier.py::verify_assertion` itself unconditionally indexes, e.g. `payload["not_before"]`, never a `.get()`). */
    val REQUIRED: Set<String> = setOf(
        "product_code", "platform", "installation_public_id", "device_key_fingerprint",
        "not_before", "expires_at", "license_status", "installation_status", "subscription_status",
        "offline_policy", "contract_version",
    )

    val FORBIDDEN_MARKERS: Set<String> = setOf(
        "license_key", "key_secret", "pepper", "password", "card_number", "bank_account",
        "patient", "medical_note", "clinical_note", "diagnosis", "prescription", "appointment",
        "invoice_total", "sale_total", "stock_quantity", "local_database",
    )
}
