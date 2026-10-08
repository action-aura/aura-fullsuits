package com.actionaura.retail.updates

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import java.io.File

/**
 * What the in-app updater knows and is doing, as one value the Settings card
 * can render. Pure data (no Android types) so the whole decision flow in
 * [UpdateFlow] is testable on the plain JVM.
 *
 * The one rule behind every type here: an [UpdateOffer] only ever comes from
 * the embedded Python backend's /api/updates/_internal/verify-descriptor
 * route, which checked Owner's signature, bound the answer to THIS install and
 * THIS request, and refused a version that is not newer. Kotlin never builds
 * an offer out of the raw Owner JSON -- the checksum, size and version it
 * downloads against are the ones Python vouched for, not the ones that
 * happened to arrive on the wire.
 */
data class UpdateOffer(
    val productVersionId: String,
    val version: String,
    /** Null when Owner did not publish a build number; then versionCode is only compared to "newer". */
    val buildNumber: Int?,
    val checksumSha256: String,
    val sizeBytes: Long,
    /** Owner data, shown as-is. */
    val releaseNotes: String?,
    val updateRequired: Boolean,
    val belowMinSupported: Boolean,
)

sealed interface UpdateState {
    /** Nothing has been checked yet this session. */
    data object Idle : UpdateState

    /** The updater is off for a stated reason ([UpdateErrorCode.NOT_CONFIGURED] / [UpdateErrorCode.NOT_ACTIVATED]). */
    data class Disabled(val reason: String) : UpdateState

    data object Checking : UpdateState
    data object UpToDate : UpdateState
    data class Available(val offer: UpdateOffer) : UpdateState
    data class Downloading(val received: Long, val total: Long) : UpdateState

    /** Downloaded, hash-checked and archive-checked; waiting for the user to press "Update now". */
    data class Ready(val file: File, val offer: UpdateOffer) : UpdateState

    /** Android will not install from this app until the user allows it in Settings. */
    data class NeedsInstallPermission(val file: File, val offer: UpdateOffer) : UpdateState

    data object Installing : UpdateState
    data class Error(val code: String) : UpdateState
}

/**
 * Every failure the updater can show, as a stable string. The UI maps each to
 * plain words (UpdatesSection.kt); UpdatesWiringContractTest fails if a code
 * here has no message there, so a new code cannot silently render as nothing.
 */
object UpdateErrorCode {
    const val NOT_CONFIGURED = "NOT_CONFIGURED"
    const val NOT_ACTIVATED = "NOT_ACTIVATED"
    const val OWNER_UNREACHABLE = "OWNER_UNREACHABLE"
    const val LOCAL_UNAVAILABLE = "LOCAL_UNAVAILABLE"

    // Python's verdicts, passed through unchanged from verify-descriptor.
    const val DESCRIPTOR_UNTRUSTED = "DESCRIPTOR_UNTRUSTED"
    const val DESCRIPTOR_MISMATCH = "DESCRIPTOR_MISMATCH"
    const val DESCRIPTOR_INVALID = "DESCRIPTOR_INVALID"
    const val OWNER_REFUSED = "OWNER_REFUSED"

    const val AUTHORIZE_REFUSED = "AUTHORIZE_REFUSED"
    const val DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    const val DOWNLOAD_CORRUPT = "DOWNLOAD_CORRUPT"
    const val SIGNATURE_MISMATCH = "SIGNATURE_MISMATCH"
    const val WRONG_PACKAGE = "WRONG_PACKAGE"
    const val NOT_NEWER = "NOT_NEWER"
    const val INSTALLER_TAMPERED = "INSTALLER_TAMPERED"
    const val INSTALL_FAILED = "INSTALL_FAILED"

    val ALL: List<String> = listOf(
        NOT_CONFIGURED, NOT_ACTIVATED, OWNER_UNREACHABLE, LOCAL_UNAVAILABLE,
        DESCRIPTOR_UNTRUSTED, DESCRIPTOR_MISMATCH, DESCRIPTOR_INVALID, OWNER_REFUSED,
        AUTHORIZE_REFUSED, DOWNLOAD_FAILED, DOWNLOAD_CORRUPT, SIGNATURE_MISMATCH,
        WRONG_PACKAGE, NOT_NEWER, INSTALLER_TAMPERED, INSTALL_FAILED,
    )
}

/** What the local verify-descriptor route said. */
sealed interface DescriptorVerdict {
    data class Offer(val offer: UpdateOffer) : DescriptorVerdict
    data object NoUpdate : DescriptorVerdict
    data class Refused(val errorCode: String) : DescriptorVerdict
}

/**
 * Pure parse of the verify-descriptor reply. Fails CLOSED: anything that is
 * not exactly an OFFER with every field we download against, or exactly a
 * NO_UPDATE, is a refusal -- never a guess at an offer.
 *
 * 409 carries Python's own error_code; an unknown code, a 401, a 5xx or an
 * unparseable body all collapse to [UpdateErrorCode.LOCAL_UNAVAILABLE].
 */
fun parseDescriptorVerdict(httpCode: Int, body: String): DescriptorVerdict {
    val json: JsonObject = try {
        JsonParser.parseString(body).asJsonObject
    } catch (_: Exception) {
        return DescriptorVerdict.Refused(UpdateErrorCode.LOCAL_UNAVAILABLE)
    }
    fun str(o: JsonObject, key: String): String? =
        o.get(key)?.takeUnless { it.isJsonNull }?.let { if (it.isJsonPrimitive) it.asString else null }

    if (httpCode == 409) {
        val code = str(json, "error_code")
        val known = setOf(
            UpdateErrorCode.DESCRIPTOR_UNTRUSTED, UpdateErrorCode.DESCRIPTOR_MISMATCH,
            UpdateErrorCode.DESCRIPTOR_INVALID, UpdateErrorCode.NOT_ACTIVATED, UpdateErrorCode.OWNER_REFUSED,
        )
        return DescriptorVerdict.Refused(if (code in known) code!! else UpdateErrorCode.LOCAL_UNAVAILABLE)
    }
    if (httpCode != 200) return DescriptorVerdict.Refused(UpdateErrorCode.LOCAL_UNAVAILABLE)

    return when (str(json, "result")) {
        "NO_UPDATE" -> DescriptorVerdict.NoUpdate
        "OFFER" -> {
            val o = try { json.getAsJsonObject("offer") } catch (_: Exception) { null }
                ?: return DescriptorVerdict.Refused(UpdateErrorCode.DESCRIPTOR_INVALID)
            val id = str(o, "product_version_id")
            val version = str(o, "version")
            val checksum = str(o, "checksum_sha256")
            val size = try { o.get("size_bytes")?.takeUnless { it.isJsonNull }?.asLong } catch (_: Exception) { null }
            val build = try { o.get("build_number")?.takeUnless { it.isJsonNull }?.asInt } catch (_: Exception) { null }
            if (id.isNullOrBlank() || version.isNullOrBlank() || checksum.isNullOrBlank() || size == null || size <= 0L) {
                DescriptorVerdict.Refused(UpdateErrorCode.DESCRIPTOR_INVALID)
            } else {
                DescriptorVerdict.Offer(
                    UpdateOffer(
                        productVersionId = id,
                        version = version,
                        buildNumber = build,
                        checksumSha256 = checksum,
                        sizeBytes = size,
                        releaseNotes = str(o, "release_notes"),
                        updateRequired = o.get("update_required")?.takeIf { it.isJsonPrimitive }?.asBoolean ?: false,
                        belowMinSupported = o.get("below_min_supported")?.takeIf { it.isJsonPrimitive }?.asBoolean ?: false,
                    )
                )
            }
        }
        else -> DescriptorVerdict.Refused(UpdateErrorCode.DESCRIPTOR_INVALID)
    }
}
