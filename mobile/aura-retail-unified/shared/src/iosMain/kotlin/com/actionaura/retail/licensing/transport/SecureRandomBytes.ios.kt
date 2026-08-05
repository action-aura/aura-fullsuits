package com.actionaura.retail.licensing.transport

import kotlinx.cinterop.ExperimentalForeignApi
import kotlinx.cinterop.allocArray
import kotlinx.cinterop.get
import kotlinx.cinterop.memScoped
import platform.Security.SecRandomCopyBytes
import platform.Security.kSecRandomDefault
import platform.posix.uint8_tVar

/**
 * Real iOS actual using Apple's `SecRandomCopyBytes` CSPRNG. Written,
 * not merely stubbed -- but **not verified** on this Windows host, per
 * this whole initiative's own standing disclosure
 * (`ios-build-readiness-plan.md`, `ios-activation-orchestration-
 * readiness.md`): no macOS/Xcode exists here to confirm real
 * Kotlin/Native compilation or execution. Real code, real intent,
 * honestly unconfirmed until built on a Mac.
 */
@OptIn(ExperimentalForeignApi::class)
actual fun secureRandomBytes(size: Int): ByteArray = memScoped {
    val buffer = allocArray<uint8_tVar>(size)
    SecRandomCopyBytes(kSecRandomDefault, size.toULong(), buffer)
    ByteArray(size) { index -> buffer[index].toByte() }
}
