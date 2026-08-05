package com.actionaura.retail.licensing.transport

import java.security.SecureRandom

private val androidSecureRandom = SecureRandom()

actual fun secureRandomBytes(size: Int): ByteArray {
    val bytes = ByteArray(size)
    androidSecureRandom.nextBytes(bytes)
    return bytes
}
