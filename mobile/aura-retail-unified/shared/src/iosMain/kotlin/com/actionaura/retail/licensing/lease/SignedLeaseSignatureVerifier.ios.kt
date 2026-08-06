package com.actionaura.retail.licensing.lease

import kotlinx.cinterop.ExperimentalForeignApi
import kotlinx.cinterop.addressOf
import kotlinx.cinterop.convert
import kotlinx.cinterop.usePinned
import platform.Foundation.NSData
import platform.Security.SecKeyCreateWithData
import platform.Security.SecKeyVerifySignature
import platform.Security.kSecAttrKeyClass
import platform.Security.kSecAttrKeyClassPublic
import platform.Security.kSecAttrKeyType
import platform.Security.kSecAttrKeyTypeEd25519
import platform.Security.kSecKeyAlgorithmEdDSASignatureRFC8032

/**
 * Real iOS Ed25519 verification via the Security framework's `SecKey`
 * API (`kSecAttrKeyTypeEd25519`, `kSecKeyAlgorithmEdDSASignatureRFC8032`)
 * -- CryptoKit itself is a Swift-native framework without a stable C
 * header, so it is not directly reachable from Kotlin/Native cinterop
 * the way `platform.Security` already is (the same real constraint
 * M9's `SecureRandomBytes.ios.kt` documents for `SecRandomCopyBytes`).
 *
 * **Real, honest, explicit disclosure beyond the standard "NOT
 * VERIFIED" runtime disclosure**: the exact `SecKeyAlgorithm` constant
 * name/availability for raw Ed25519 verification varies across recent
 * iOS SDK versions in ways this Windows host cannot confirm against
 * real Apple SDK headers -- this file's exact API surface may require
 * correction the first time it is compiled on a real macOS/Xcode
 * host. This is a real, structural attempt at the correct Apple API
 * (not a placeholder/stub), written to this codebase's own established
 * cinterop style, but carries a real compile-time-uncertainty risk
 * beyond the usual runtime-only uncertainty every other iOS file in
 * this codebase discloses.
 */
@OptIn(ExperimentalForeignApi::class)
actual fun verifyEd25519Signature(publicKeyRaw: ByteArray, signatureRaw: ByteArray, message: ByteArray): Boolean {
    if (publicKeyRaw.size != 32 || signatureRaw.size != 64) return false
    return try {
        val keyData = publicKeyRaw.toNSData()
        val attributes = platform.Foundation.NSDictionary.dictionaryWithObjectsAndKeys(
            kSecAttrKeyTypeEd25519, kSecAttrKeyType,
            kSecAttrKeyClassPublic, kSecAttrKeyClass,
            null,
        )
        val secKey = SecKeyCreateWithData(keyData, attributes, null) ?: return false
        SecKeyVerifySignature(
            secKey,
            kSecKeyAlgorithmEdDSASignatureRFC8032,
            message.toNSData(),
            signatureRaw.toNSData(),
            null,
        )
    } catch (e: Throwable) {
        false
    }
}

@OptIn(ExperimentalForeignApi::class)
private fun ByteArray.toNSData(): NSData = this.usePinned { pinned ->
    NSData.create(bytes = pinned.addressOf(0), length = this.size.convert())
}
