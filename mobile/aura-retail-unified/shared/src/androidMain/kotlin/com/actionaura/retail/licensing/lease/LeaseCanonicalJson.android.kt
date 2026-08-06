package com.actionaura.retail.licensing.lease

import java.text.Normalizer

/** Real Android/JVM NFC normalization, matching Python's `unicodedata.normalize("NFC", value)` exactly -- both are Unicode Normalization Form C. */
internal actual fun String.normalizeNfc(): String = Normalizer.normalize(this, Normalizer.Form.NFC)
