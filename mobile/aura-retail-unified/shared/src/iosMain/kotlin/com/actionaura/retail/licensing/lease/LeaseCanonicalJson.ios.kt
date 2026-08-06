package com.actionaura.retail.licensing.lease

import platform.Foundation.NSString
import platform.Foundation.precomposedStringWithCanonicalMapping

/** Real iOS/Darwin NFC normalization via Foundation's own canonical-composition mapping -- matches Python's `unicodedata.normalize("NFC", value)`. Real, written, NOT VERIFIED on this host (no macOS/Xcode -- `ios-secure-storage-runtime-validation-plan.md`'s own standing disclosure pattern). */
internal actual fun String.normalizeNfc(): String = (this as NSString).precomposedStringWithCanonicalMapping
